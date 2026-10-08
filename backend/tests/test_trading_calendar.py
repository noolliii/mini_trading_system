"""거래일 계산기. DB 없이, 저장해 둔 실제 KIS 휴장일 응답으로 돕니다.

2026-10-09(금) 는 한글날이라 평일인데도 휴장입니다 — 주말만 걸러서는
못 잡는 경우라, "요일로 계산"과 실제 캘린더가 갈리는 지점을 그대로 씁니다.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.trading_calendar import next_trading_day_after, trading_day_before

FIXTURE = Path(__file__).parent / "fixtures" / "holiday_response.json"


@pytest.fixture(scope="module")
def open_days() -> list[date]:
    """2026-10-01 ~ 10-24, 실전 도메인에서 받은 실제 개장일 목록입니다."""
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [
        date(int(d[:4]), int(d[4:6]), int(d[6:]))
        for d in (row["bass_dt"] for row in body["output"])
        if _is_open(body, d)
    ]


def _is_open(body: dict, bass_dt: str) -> bool:
    row = next(r for r in body["output"] if r["bass_dt"] == bass_dt)
    return row["opnd_yn"] == "Y"


def test_fixture_marks_hangul_day_as_closed() -> None:
    """10/9(금) 한글날은 평일인데도 휴장입니다 — 공휴일 fixture 가 맞는지 확인."""
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = next(r for r in body["output"] if r["bass_dt"] == "20261009")
    assert row["opnd_yn"] == "N"
    assert row["wday_dvsn_cd"] != "07"  # 토요일이 아닙니다 — 주말이라서 쉬는 게 아닙니다


def test_fixture_holds_no_credentials() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    assert "appkey" not in raw
    assert "appsecret" not in raw
    assert "authorization" not in raw.lower()


def test_one_trading_day_before_skips_the_weekend(open_days: list[date]) -> None:
    """10/12(월) 바로 전 거래일은 10/8(목) 이어야 합니다 — 10/9(금,한글날)·주말을 건너뜁니다."""
    assert trading_day_before(open_days, date(2026, 10, 12), 1) == date(2026, 10, 8)


def test_naive_day_before_would_land_on_a_holiday(open_days: list[date]) -> None:
    """같은 질문을 "그냥 하루 전"으로 하면 공휴일에 떨어집니다 — 캘린더가 왜 필요한지 보여줍니다."""
    naive = date(2026, 10, 12) - timedelta(days=1)
    assert naive == date(2026, 10, 11)  # 일요일
    assert naive not in open_days

    real = trading_day_before(open_days, date(2026, 10, 12), 1)
    assert real != naive


def test_zero_trading_days_before_a_holiday_looks_back(open_days: list[date]) -> None:
    """한글날(10/9) 자신은 개장일이 아니므로, 0번째도 그 전 가장 가까운 개장일입니다."""
    assert trading_day_before(open_days, date(2026, 10, 9), 0) == date(2026, 10, 8)


def test_zero_trading_days_before_an_open_day_is_itself(open_days: list[date]) -> None:
    assert trading_day_before(open_days, date(2026, 10, 8), 0) == date(2026, 10, 8)


def test_running_out_of_history_returns_none(open_days: list[date]) -> None:
    """fixture 는 10/1 부터입니다. 그보다 훨씬 이전으로 가려 하면 모른다고 답합니다."""
    assert trading_day_before(open_days, date(2026, 10, 1), 100) is None


def test_negative_n_is_rejected(open_days: list[date]) -> None:
    with pytest.raises(ValueError):
        trading_day_before(open_days, date(2026, 10, 12), -1)


def test_next_trading_day_after_a_holiday_weekend_run(open_days: list[date]) -> None:
    """10/9(금,한글날) 다음 거래일은 10/12(월) 입니다 — 주말까지 한 번에 건너뜁니다."""
    assert next_trading_day_after(open_days, date(2026, 10, 9)) == date(2026, 10, 12)


def test_next_trading_day_after_an_open_day(open_days: list[date]) -> None:
    """10/8(목) 다음 개장일은 10/9(한글날)가 아니라 10/12(월) 입니다."""
    assert next_trading_day_after(open_days, date(2026, 10, 8)) == date(2026, 10, 12)


def test_next_trading_day_after_an_ordinary_open_day(open_days: list[date]) -> None:
    """공휴일이 안 끼면 보통은 그냥 다음 날입니다."""
    assert next_trading_day_after(open_days, date(2026, 10, 6)) == date(2026, 10, 7)


def test_next_trading_day_beyond_known_range_is_none(open_days: list[date]) -> None:
    assert next_trading_day_after(open_days, date(2026, 10, 24)) is None

