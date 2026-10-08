"""Discord 알림. 네트워크를 타지 않고, 웹훅 주소가 안 새는지만 봅니다."""

import logging

import httpx
import pytest

from app import discord


@pytest.fixture(autouse=True)
def no_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값은 "설정 안 됨" 입니다 — 수업 환경의 기본 상태와 같습니다."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)


def test_not_configured_by_default() -> None:
    assert discord.configured() is False


async def test_send_summary_is_a_no_op_without_a_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """주소가 없으면 조용히 건너뜁니다 — 예외도, 네트워크 호출도 없습니다."""
    called = False

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(httpx.AsyncClient, "post", fail_if_called)

    await discord.send_summary(3, 1, 0, ["005930: 5건"])

    assert called is False


async def test_send_summary_posts_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    assert discord.configured() is True

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 204

    async def fake_post(self: httpx.AsyncClient, url: str, json: dict[str, str]) -> FakeResponse:
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await discord.send_summary(3, 1, 2, ["005930: 5건", "000660: 실패 — 거절"])

    assert captured["url"] == "https://discord.com/api/webhooks/x/y"
    content = captured["json"]["content"]
    assert "성공 3" in content
    assert "실패 1" in content
    assert "건너뜀 2" in content
    assert "005930" in content


async def test_webhook_url_never_appears_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """주소 자체가 비밀입니다 — 성공해도 실패해도 로그에 주소를 안 남깁니다."""
    secret_url = "https://discord.com/api/webhooks/123/ABCDEF-super-secret-token"
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", secret_url)

    class FakeResponse:
        status_code = 204

    async def fake_post(self: httpx.AsyncClient, url: str, json: dict[str, str]) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with caplog.at_level(logging.DEBUG):
        await discord.send_summary(1, 0, 0, [])

    assert secret_url not in caplog.text


async def test_network_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """알림이 실패해도 예외를 밖으로 던지지 않습니다 — 수집 자체는 끝난 일입니다."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")

    async def raise_connect_error(self: httpx.AsyncClient, url: str, json: dict[str, str]) -> None:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "post", raise_connect_error)

    await discord.send_summary(1, 0, 0, [])  # 예외 없이 끝나야 합니다
