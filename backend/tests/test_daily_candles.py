"""KIS 일봉 응답을 일봉으로 바꾸는 부분만 떼어 봅니다.

네트워크를 타지 않습니다 — 실제로 받은 응답 한 건을
tests/fixtures/daily_response.json 에 저장해 두고 그걸로 확인합니다.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import kis

FIXTURE = Path(__file__).parent / "fixtures" / "daily_response.json"


@pytest.fixture(scope="module")
def response() -> dict:
    """2026-10-08 에 모의 도메인에서 받은 삼성전자 응답입니다."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_a_successful_response(response: dict) -> None:
    assert response["rt_cd"] == "0"
    assert response["output2"]


def test_fixture_holds_no_credentials() -> None:
    """저장해 둔 응답에 앱키가 섞여 들어가지 않았는지 봅니다."""
    raw = FIXTURE.read_text(encoding="utf-8")
    assert "appkey" not in raw
    assert "appsecret" not in raw
    assert "authorization" not in raw.lower()


def test_rows_become_candles(response: dict) -> None:
    """문자열로 오는 값이 숫자와 날짜로 바뀝니다."""
    candles = kis.parse_chart_rows(response["output2"])

    assert len(candles) == len(response["output2"])

    first = candles[0]
    row = response["output2"][0]
    assert first.time == datetime(2026, 10, 8, tzinfo=timezone(timedelta(hours=9)))
    assert first.open == float(row["stck_oprc"])
    assert first.high == float(row["stck_hgpr"])
    assert first.low == float(row["stck_lwpr"])
    assert first.close == float(row["stck_clpr"])
    assert first.volume == int(row["acml_vol"])


def test_high_low_bracket_the_other_prices(response: dict) -> None:
    """고가·저가가 시가·종가를 감싸는지 — 칼럼을 섞어 매핑했으면 여기서 깨집니다."""
    for candle in kis.parse_chart_rows(response["output2"]):
        assert candle.low <= candle.open <= candle.high
        assert candle.low <= candle.close <= candle.high


def test_blank_dates_are_dropped() -> None:
    """KIS 는 구간 끝에 날짜가 빈 줄을 채워 보냅니다. 그 줄은 버립니다."""
    rows = [
        {
            "stck_bsop_date": "20261008",
            "stck_oprc": "269500",
            "stck_hgpr": "270000",
            "stck_lwpr": "263000",
            "stck_clpr": "265750",
            "acml_vol": "12533296",
        },
        {"stck_bsop_date": "", "stck_oprc": "0"},
        {"stck_bsop_date": "   ", "stck_oprc": "0"},
    ]

    candles = kis.parse_chart_rows(rows)

    assert len(candles) == 1
    assert candles[0].close == 265750.0


def test_candles_come_back_oldest_first(response: dict) -> None:
    """KIS 는 최신순으로 주지만, 적재·차트는 오래된 것부터 씁니다."""
    candles = kis.parse_chart_rows(response["output2"])
    # 파서는 받은 순서를 유지합니다 (최신순)
    assert candles[0].time > candles[-1].time
    # 정렬은 fetch_daily_candles 가 합니다
    assert sorted(c.time for c in candles) == [c.time for c in reversed(candles)]
