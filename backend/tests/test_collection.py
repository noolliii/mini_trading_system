"""증분 수집 일괄 실행. KIS 도 Discord 도 가짜로 갈아 끼우고 로직만 봅니다.

이 함수는 API 엔드포인트(버튼)와 스케줄러(새벽)가 똑같이 거치는 곳이라,
여기 하나만 제대로 보면 양쪽 다 보장됩니다.
"""

from datetime import date, datetime, timezone

import pytest

from app import collection, discord, kis
from app.schemas import CollectResult


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 DB 커넥션 풀을 쓰지 않습니다 — db.acquire 를 가짜 커넥션으로 바꿔 끼웁니다."""

    class FakeConn:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows
            self.executed: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            return self.rows

        async def fetchval(self, query: str, *args: object) -> object:
            if "is_open" in query:
                return True  # 기본은 "오늘은 개장일" 입니다
            return None

        async def execute(self, query: str, *args: object) -> None:
            self.executed.append((query, args))

    fake = FakeConn([{"ticker": "005930"}, {"ticker": "000660"}])

    class FakeAcquire:
        async def __aenter__(self) -> FakeConn:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(collection.db, "acquire", lambda: FakeAcquire())


def candle_result(ticker: str, fetched: int) -> CollectResult:
    now = datetime.now(timezone.utc)
    return CollectResult(
        ticker=ticker, mode="paper", fetched=fetched, inserted=fetched,
        first=now if fetched else None, last=now if fetched else None,
    )


async def test_runs_every_ticker_and_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_collect(ticker: str, mode: str) -> CollectResult:
        calls.append(ticker)
        return candle_result(ticker, fetched=1)

    monkeypatch.setattr(collection, "collect_incremental_ticker", fake_collect)
    monkeypatch.setattr(collection, "_is_today_a_trading_day", _always_open)

    sent: dict[str, object] = {}

    async def fake_send(success: int, failed: int, skipped: int, details: list[str]) -> None:
        sent.update(success=success, failed=failed, skipped=skipped, details=details)

    monkeypatch.setattr(discord, "send_summary", fake_send)

    result = await collection.run_incremental_for_all_stocks("paper")

    assert calls == ["005930", "000660"]
    assert result["success"] == 2
    assert result["failed"] == 0
    assert sent["success"] == 2


async def test_one_ticker_failing_does_not_stop_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """005930 이 거절당해도 000660 은 계속 받아야 합니다."""
    calls: list[str] = []

    async def fake_collect(ticker: str, mode: str) -> CollectResult:
        calls.append(ticker)
        if ticker == "005930":
            raise kis.KisError("거절됨")
        return candle_result(ticker, fetched=2)

    monkeypatch.setattr(collection, "collect_incremental_ticker", fake_collect)
    monkeypatch.setattr(collection, "_is_today_a_trading_day", _always_open)
    monkeypatch.setattr(discord, "send_summary", _noop_send)

    result = await collection.run_incremental_for_all_stocks("paper")

    assert calls == ["005930", "000660"]  # 둘 다 시도됩니다
    assert result["success"] == 1
    assert result["failed"] == 1


async def test_a_non_kis_exception_does_not_kill_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """네트워크 타임아웃처럼 KisError 가 아닌 예외도 한 종목만 실패로 끝나야 합니다.

    이게 없으면 500으로 전체 요청이 죽고, 무인으로 도는 스케줄러에서는
    나머지 종목을 영영 못 받습니다 — 실제로 KIS 연결 타임아웃으로 겪었던 버그입니다.
    """
    calls: list[str] = []

    async def fake_collect(ticker: str, mode: str) -> CollectResult:
        calls.append(ticker)
        if ticker == "005930":
            raise RuntimeError("완전히 예상 못한 버그")
        return candle_result(ticker, fetched=2)

    monkeypatch.setattr(collection, "collect_incremental_ticker", fake_collect)
    monkeypatch.setattr(collection, "_is_today_a_trading_day", _always_open)
    monkeypatch.setattr(discord, "send_summary", _noop_send)

    result = await collection.run_incremental_for_all_stocks("paper")  # 예외 없이 끝나야 함

    assert calls == ["005930", "000660"]
    assert result["success"] == 1
    assert result["failed"] == 1


async def test_skips_everything_on_a_holiday(monkeypatch: pytest.MonkeyPatch) -> None:
    """오늘이 캘린더상 휴장이면 종목을 하나도 건드리지 않습니다."""
    calls: list[str] = []

    async def fake_collect(ticker: str, mode: str) -> CollectResult:
        calls.append(ticker)
        return candle_result(ticker, fetched=1)

    monkeypatch.setattr(collection, "collect_incremental_ticker", fake_collect)
    monkeypatch.setattr(collection, "_is_today_a_trading_day", _always_closed)
    monkeypatch.setattr(discord, "send_summary", _noop_send)

    result = await collection.run_incremental_for_all_stocks("paper")

    assert calls == []
    assert result["skipped_holiday"] is True


async def test_unknown_calendar_proceeds_anyway(monkeypatch: pytest.MonkeyPatch) -> None:
    """캘린더가 오늘을 모르면(아직 안 받았으면) 안전하게 "그래도 시도" 쪽으로 둡니다."""
    calls: list[str] = []

    async def fake_collect(ticker: str, mode: str) -> CollectResult:
        calls.append(ticker)
        return candle_result(ticker, fetched=1)

    monkeypatch.setattr(collection, "collect_incremental_ticker", fake_collect)
    monkeypatch.setattr(collection, "_is_today_a_trading_day", _unknown)
    monkeypatch.setattr(discord, "send_summary", _noop_send)

    result = await collection.run_incremental_for_all_stocks("paper")

    assert calls == ["005930", "000660"]
    assert result["skipped_holiday"] is False


async def _always_open() -> bool | None:
    return True


async def _always_closed() -> bool | None:
    return False


async def _unknown() -> bool | None:
    return None


async def _noop_send(success: int, failed: int, skipped: int, details: list[str]) -> None:
    return None
