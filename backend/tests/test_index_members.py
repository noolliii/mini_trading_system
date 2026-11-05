"""코스피100 편입 이력 필터링. 생존편향을 없애는 핵심 로직이라 따로 테스트합니다.

_was_member_on 은 날짜 하나 대 구간 목록만 보는 순수 함수라 DB 없이 돕니다.
_fetch_universe_candles 는 FakeConn 으로 daily_candles · index_members 조회를
흉내 내 — 실제 커넥션 없이 "그날 편입되어 있던 종목만 남는지"를 확인합니다.
"""

from datetime import date, datetime, timezone

import pytest

from app.main import _fetch_universe_candles, _was_member_on


def test_within_open_ended_interval_is_member() -> None:
    """end_date 가 None 이면 지금도 편입 중 — start 이후는 전부 True 입니다."""
    intervals = [(date(2020, 1, 2), None)]
    assert _was_member_on(intervals, date(2020, 1, 2)) is True
    assert _was_member_on(intervals, date(2026, 11, 4)) is True


def test_before_start_is_not_member() -> None:
    intervals = [(date(2021, 7, 1), None)]
    assert _was_member_on(intervals, date(2021, 6, 30)) is False


def test_end_date_is_exclusive() -> None:
    """end_date 당일부터 빠집니다 — 그 전날까지만 편입된 것으로 칩니다."""
    intervals = [(date(2020, 1, 2), date(2020, 7, 1))]
    assert _was_member_on(intervals, date(2020, 6, 30)) is True
    assert _was_member_on(intervals, date(2020, 7, 1)) is False


def test_gap_between_two_intervals_is_not_member() -> None:
    """한 번 빠졌다가 다시 들어온 종목 — 빠져 있던 기간엔 후보가 아닙니다."""
    intervals = [(date(2020, 1, 2), date(2020, 7, 1)), (date(2025, 7, 1), None)]
    assert _was_member_on(intervals, date(2020, 1, 2)) is True
    assert _was_member_on(intervals, date(2022, 1, 1)) is False  # 빠져 있던 기간
    assert _was_member_on(intervals, date(2025, 7, 1)) is True  # 재편입


def test_no_intervals_is_never_a_member() -> None:
    assert _was_member_on([], date(2024, 1, 1)) is False


class FakeConn:
    """daily_candles · index_members 조회를 쿼리 문자열로 구분해 흉내 냅니다."""

    def __init__(self, candle_rows: list[dict[str, object]], member_rows: list[dict[str, object]]) -> None:
        self.candle_rows = candle_rows
        self.member_rows = member_rows

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        if "daily_candles" in query:
            return self.candle_rows
        if "index_members" in query:
            return self.member_rows
        raise AssertionError(f"예상 못한 쿼리: {query}")


def _candle(ticker: str, day: date, price: float = 100.0) -> dict[str, object]:
    return {
        "ticker": ticker,
        "time": datetime(day.year, day.month, day.day, tzinfo=timezone.utc),
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1000,
    }


async def test_removed_ticker_is_excluded_after_its_end_date() -> None:
    """지금은 코스피100 에서 빠진 종목이라도, 편입 중이었던 날짜의 일봉은 남습니다."""
    candles = [
        _candle("000150", date(2020, 3, 1)),  # 편입 중 — 남아야 함
        _candle("000150", date(2021, 1, 1)),  # 편출 후 — 빠져야 함
    ]
    members = [{"ticker": "000150", "start_date": date(2020, 1, 2), "end_date": date(2020, 7, 1)}]

    result = await _fetch_universe_candles(
        FakeConn(candles, members), days=3650, start=None, end=None
    )

    assert len(result["000150"]) == 1
    assert result["000150"][0].time.date() == date(2020, 3, 1)


async def test_ticker_with_no_index_history_is_dropped_entirely() -> None:
    """index_members 에 이력이 아예 없는 종목(수업용 시드 등)은 통째로 제외됩니다."""
    candles = [_candle("247540", date(2024, 1, 1))]  # KOSDAQ 시드 종목, 코스피100 이력 없음

    result = await _fetch_universe_candles(FakeConn(candles, []), days=3650, start=None, end=None)

    assert "247540" not in result


async def test_wanted_filter_still_requires_index_membership() -> None:
    """종목 선택 체크박스(tickers 파라미터)로 골라도, 그 시점 비편입 구간은 여전히 빠집니다."""
    candles = [
        _candle("005930", date(2019, 1, 1)),  # 관측 시작 이전 — 편입 이력 밖
        _candle("005930", date(2021, 1, 1)),
    ]
    members = [{"ticker": "005930", "start_date": date(2020, 1, 2), "end_date": None}]

    result = await _fetch_universe_candles(
        FakeConn(candles, members), days=3650, start=None, end=None, wanted={"005930"}
    )

    assert len(result["005930"]) == 1
    assert result["005930"][0].time.date() == date(2021, 1, 1)
