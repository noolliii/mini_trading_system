"""거래일 계산. DB 를 보지 않는 순수 함수라, 캘린더 목록만 쥐여주고 테스트합니다.

주말만 건너뛰는 게 아니라 설날·추석 같은 평일 공휴일도 걸러야 해서,
"요일로 계산" 하지 않고 KIS 가 내려준 실제 개장일 목록을 봅니다.
"""

from datetime import date


def trading_day_before(open_days: list[date], base: date, n: int) -> date | None:
    """base 로부터 n 거래일 전 날짜.

    n=0 이면 base 가 개장일일 때 base 자신, 아니면 그 전 가장 가까운 개장일.
    open_days 에 그만큼 과거가 없으면(캘린더를 못 받아온 구간) None 입니다 —
    "모른다"와 "그런 날이 없다"를 구분하려고 예외 대신 None 을 돌려줍니다.
    """
    if n < 0:
        raise ValueError("n 은 0 이상이어야 합니다")

    candidates = sorted((d for d in open_days if d <= base), reverse=True)
    if n < len(candidates):
        return candidates[n]
    return None


def next_trading_day_after(open_days: list[date], base: date) -> date | None:
    """base 다음 개장일. 증분 수집이 "마지막 적재일 다음 거래일"을 구할 때 씁니다."""
    candidates = sorted(d for d in open_days if d > base)
    return candidates[0] if candidates else None
