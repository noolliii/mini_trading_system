"""접근 로그에서 계좌번호가 가려지는지 봅니다.

uvicorn 이 로그 한 줄을 만들 때 쓰는 모양(다섯 짝)을 그대로 흉내 내서
필터만 떼어 확인합니다.
"""

import logging

from app.access_log import MaskAccountNumbers, mask_account_path


def access_record(path: str) -> logging.LogRecord:
    """uvicorn.access 가 만드는 것과 같은 모양의 기록을 만듭니다."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("172.18.0.1:48532", "DELETE", path, "1.1", 404),
        exc_info=None,
    )


def test_account_number_keeps_only_the_last_four() -> None:
    assert mask_account_path("/api/accounts/50012345-01") == "/api/accounts/••••-5-01"


def test_short_value_is_hidden_entirely() -> None:
    """네 자리 이하면 남길 뒤 네 자리가 없습니다. 전부 가립니다."""
    assert mask_account_path("/api/accounts/123") == "/api/accounts/••••"


def test_percent_encoded_number_is_masked_too() -> None:
    assert "50012345" not in mask_account_path("/api/accounts/50012345%2D01")


def test_other_paths_are_untouched() -> None:
    """계좌가 아닌 경로는 손대지 않습니다 — 로그를 못 읽게 만들면 안 됩니다."""
    for path in ("/api/accounts", "/api/users/7", "/api/candles?ticker=005930", "/health"):
        assert mask_account_path(path) == path


def test_filter_rewrites_the_record_in_place() -> None:
    record = access_record("/api/accounts/50012345-01")

    assert MaskAccountNumbers().filter(record) is True

    line = record.getMessage()
    assert "50012345-01" not in line
    assert "••••" in line
    # 나머지 정보는 그대로 남아야 합니다
    assert "DELETE" in line
    assert "404" in line


def test_filter_leaves_unrelated_records_alone() -> None:
    record = access_record("/api/candles?ticker=005930")
    before = record.getMessage()

    MaskAccountNumbers().filter(record)

    assert record.getMessage() == before


def test_filter_survives_an_unexpected_record_shape() -> None:
    """모양이 다르면 아무것도 하지 않고 통과시킵니다 (로그를 잃지 않습니다)."""
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="그냥 문장 하나",
        args=None,
        exc_info=None,
    )

    assert MaskAccountNumbers().filter(record) is True
    assert record.getMessage() == "그냥 문장 하나"
