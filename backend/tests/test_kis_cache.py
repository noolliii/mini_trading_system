"""토큰 캐시가 정말 아껴 쓰는지 봅니다.

KIS 서버에는 붙지 않습니다. 발급 함수만 가짜로 갈아 끼우고
「발급 요청이 몇 번 나갔는지」를 셉니다.
"""

import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import kis


@pytest.fixture(autouse=True)
def clean_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """테스트끼리 캐시를 물려받지 않게 하고, 파일 캐시도 임시 폴더로 돌립니다.

    임시 폴더로 바꾸지 않으면 돌고 있는 서버가 남긴 진짜 토큰을 읽어 버립니다.
    """
    monkeypatch.setattr(kis, "TOKEN_DIR", tmp_path / "kis-tokens")
    kis.reset_cache()
    yield
    kis.reset_cache()


def fake_issuer(calls: list[str], expires_in: int = 86400) -> Callable[[str], object]:
    """KIS 대신 가짜 토큰을 돌려주면서, 불린 횟수를 calls 에 쌓습니다."""

    async def _request_new(mode: str) -> kis._Cached:
        calls.append(mode)
        now = datetime.now(timezone.utc)
        return kis._Cached(
            token=f"fake-token-{len(calls)}-0123456789",
            issued_at=now,
            expires_at=now + timedelta(seconds=expires_in),
        )

    return _request_new


def age_cache(mode: str, expires_at: datetime) -> None:
    """만료 시각을 메모리와 파일 두 군데에서 함께 당깁니다.

    한쪽만 당기면 다른 쪽에 살아 있는 사본이 쓰여서, 재발급을 확인할 수 없습니다.
    """
    kis._cache[mode].expires_at = expires_at
    kis._save_to_disk(mode, kis._cache[mode])


async def test_second_call_reuses_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """연달아 두 번 불러도 발급 요청은 한 번만 나갑니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    first = await kis.request_access_token("paper")
    second = await kis.request_access_token("paper")

    assert len(calls) == 1
    assert first.cached is False
    assert second.cached is True
    assert first.token_preview == second.token_preview


async def test_expired_token_is_issued_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """만료 시각이 지나면 새로 받습니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    await kis.request_access_token("paper")
    # 만료를 지나간 것으로 손대 놓습니다
    age_cache("paper", datetime.now(timezone.utc) - timedelta(seconds=1))

    again = await kis.request_access_token("paper")

    assert len(calls) == 2
    assert again.cached is False


async def test_refresh_margin_counts_as_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """만료가 눈앞이면(여유분 안쪽이면) 미리 새로 받습니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    await kis.request_access_token("paper")
    age_cache("paper", datetime.now(timezone.utc) + timedelta(seconds=5))

    await kis.request_access_token("paper")

    assert len(calls) == 2


async def test_force_ignores_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """force 를 켜면 살아 있는 캐시를 버리고 새로 받습니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    await kis.request_access_token("paper")
    forced = await kis.request_access_token("paper", force=True)

    assert len(calls) == 2
    assert forced.cached is False


async def test_paper_and_live_are_cached_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """실전과 모의를 한 칸에 담지 않습니다 — 섞이면 엉뚱한 서버로 갑니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    paper = await kis.request_access_token("paper")
    live = await kis.request_access_token("live")

    assert calls == ["paper", "live"]
    assert paper.host != live.host
    assert "openapivts" in paper.host  # 모의
    assert await kis.access_token("paper") != await kis.access_token("live")


async def test_concurrent_calls_issue_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """동시에 들어와도 발급은 한 번입니다. 자물쇠가 없으면 여기서 두 번이 됩니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    await asyncio.gather(*(kis.request_access_token("paper") for _ in range(5)))

    assert len(calls) == 1


async def test_disk_cache_survives_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """메모리를 비워도(= 프로세스가 다시 떠도) 파일에 남은 토큰을 되살립니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    await kis.request_access_token("paper")
    kis.reset_cache()  # 재시작한 것과 같은 상태

    again = await kis.request_access_token("paper")

    assert len(calls) == 1
    assert again.cached is True


async def test_token_never_leaves_the_server(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """로그와 응답 어디에도 토큰 원문이 없어야 합니다."""
    calls: list[str] = []
    monkeypatch.setattr(kis, "_request_new", fake_issuer(calls))

    with caplog.at_level(logging.DEBUG):
        issued = await kis.request_access_token("paper")

    token = kis._cache["paper"].token

    assert token not in caplog.text
    assert token not in issued.model_dump_json()
    # 앞 네 글자까지만 보여줍니다
    assert issued.token_preview.startswith(token[:4])
    assert token[4:] not in issued.token_preview
