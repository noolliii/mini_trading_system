"""앱키에서 토큰까지, 그리고 일봉 조회.

app_key + app_secret → .env → 토큰 요청 → 접근 토큰 → 만료 전까지 재사용.

- 실전과 모의를 두 벌로 나눠서 읽습니다. 섞어 쓰면 엉뚱한 서버에 엉뚱한 키를 보냅니다.
- 키와 토큰은 코드에 적지 않고 서버 안에만 둡니다. 브라우저로는 발급 여부와
  만료 시각, 그리고 가려진 앞 네 글자만 내려보냅니다.
- 한 번 받은 토큰은 만료 전까지 캐시에서 재사용합니다. KIS 는 발급 횟수를
  제한하고, 모든 TR 조회가 이 캐시를 거칩니다.
- 일봉 조회는 한 번에 100건까지라 날짜를 끊어서 여러 번 부릅니다.
- 캐시는 메모리와 서버 안의 임시 파일 두 군데에 둡니다. KIS 는 토큰 발급을
  1분에 한 번으로 제한하는데, --reload 로 프로세스가 다시 뜰 때마다 메모리가
  비면 그 제한에 걸립니다. 파일은 컨테이너 안에만 있고 호스트·git 으로 나가지
  않습니다.
"""

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel

from .schemas import Candle, TradingDay

log = logging.getLogger(__name__)

HOSTS = {
    "paper": "https://openapivts.koreainvestment.com:29443",  # 모의투자
    "live": "https://openapi.koreainvestment.com:9443",  # 실전투자
}

ENV_PREFIX = {"paper": "KIS_PAPER", "live": "KIS_LIVE"}

TOKEN_PATH = "/oauth2/tokenP"

# 국내주식 기간별시세 (일/주/월/년)
CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
CHART_TR_ID = "FHKST03010100"

# 한 번에 100건까지 옵니다. 주말·공휴일이 빠지니 달력 날짜로는 넉넉히 잡습니다
CHART_PAGE_DAYS = 130

# 모의는 1초에 2건까지입니다. 끊어 부르는 사이에 숨을 돌립니다
RATE_SLEEP_SECONDS = 0.4

KST = timezone(timedelta(hours=9))

# 국내휴장일조회. 계좌와 무관한 시장 전체의 공개 정보인데도, 모의(paper) 도메인은
# 이 TR 을 거절합니다("모의투자 TR 이 아닙니다" EGW02006) — 그래서 이 함수만 실전
# 도메인을 씁니다. 조회만 하고 주문은 내지 않습니다. mode 인자를 안 받는 것 자체가
# "이 호출은 늘 live" 라는 뜻이라, 실수로 다른 모드를 넘길 길이 없습니다.
CALENDAR_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"
CALENDAR_TR_ID = "CTCA0903R"
CALENDAR_MODE = "live"

# 토큰을 남겨 두는 자리. 컨테이너 안이고, 볼륨이 아니라 바깥으로 나가지 않습니다
TOKEN_DIR = Path(tempfile.gettempdir()) / "kis-tokens"

# 만료 직전에 받은 토큰으로 조회를 보내면 도중에 끊깁니다. 이만큼 남으면 미리 새로 받습니다
REFRESH_MARGIN = timedelta(seconds=60)


class TokenIssue(BaseModel):
    """브라우저로 내려보내도 되는 것만 담습니다. access_token 자체는 들어가지 않습니다."""

    mode: str
    host: str
    issued_at: datetime
    expires_at: datetime
    expires_in: int  # 지금부터 만료까지 남은 초
    token_preview: str
    cached: bool  # True 면 캐시를 재사용했고, KIS 로 발급 요청이 나가지 않았습니다


class KisError(RuntimeError):
    """설정이 없거나 발급이 거절된 경우. 메시지에 키 값을 넣지 않습니다."""


@dataclass
class _Cached:
    """서버 안에만 두는 실제 토큰. 이 객체는 응답으로 나가지 않습니다."""

    token: str
    issued_at: datetime
    expires_at: datetime


_cache: dict[str, _Cached] = {}

# 모드마다 하나씩. 같은 모드로 동시에 들어와도 발급 요청은 한 번만 나갑니다
_locks: dict[str, asyncio.Lock] = {mode: asyncio.Lock() for mode in HOSTS}


def _store_path(mode: str) -> Path:
    return TOKEN_DIR / f"{mode}.json"


def _load_from_disk(mode: str) -> _Cached | None:
    """프로세스가 다시 떴을 때 쓰려고 남겨둔 토큰을 읽습니다. 깨져 있으면 없는 셈 칩니다."""
    try:
        raw = json.loads(_store_path(mode).read_text(encoding="utf-8"))
        return _Cached(
            token=raw["token"],
            issued_at=datetime.fromisoformat(raw["issued_at"]),
            expires_at=datetime.fromisoformat(raw["expires_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_to_disk(mode: str, entry: _Cached) -> None:
    """남기지 못해도 기능은 됩니다 — 다음에 새로 발급받을 뿐입니다."""
    path = _store_path(mode)
    try:
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "token": entry.token,
                    "issued_at": entry.issued_at.isoformat(),
                    "expires_at": entry.expires_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    except OSError as e:
        log.warning("토큰을 남기지 못했습니다: %s", e.__class__.__name__)


def credentials(mode: str) -> tuple[str, str]:
    if mode not in ENV_PREFIX:
        raise KisError("mode 는 paper 또는 live 여야 합니다")

    prefix = ENV_PREFIX[mode]
    app_key = os.getenv(f"{prefix}_APP_KEY", "").strip()
    app_secret = os.getenv(f"{prefix}_APP_SECRET", "").strip()

    if not app_key or not app_secret:
        raise KisError(f"{prefix}_APP_KEY 와 {prefix}_APP_SECRET 를 .env 에 채워 주세요")

    return app_key, app_secret


async def _guarded(call):  # type: ignore[no-untyped-def]
    """httpx 의 네트워크 예외(타임아웃·DNS 실패 등)를 KisError 하나로 모읍니다.

    이게 없으면 한 종목의 연결 실패가 KisError 가 아닌 날 예외로 튀어서,
    배치 작업(run_incremental_for_all_stocks)의 try/except 를 비껴가고
    나머지 종목까지 같이 멈춥니다.
    """
    try:
        return await call
    except httpx.HTTPError as e:
        raise KisError(f"KIS 서버에 연결할 수 없습니다 ({e.__class__.__name__})")


def mask(token: str) -> str:
    """토큰을 그대로 보여주지 않습니다. 앞 네 글자와 길이만."""
    return f"{token[:4]}… ({len(token)}자)"


def _fresh(entry: _Cached, now: datetime) -> bool:
    """지금 쓰기에 충분히 남았는가. 여유분을 뺀 시각으로 봅니다."""
    return now + REFRESH_MARGIN < entry.expires_at


async def _request_new(mode: str) -> _Cached:
    """KIS 에 토큰을 새로 요청합니다. 캐시를 보지 않습니다."""
    app_key, app_secret = credentials(mode)
    host = HOSTS[mode]

    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await _guarded(
            client.post(
                f"{host}{TOKEN_PATH}",
                json={
                    "grant_type": "client_credentials",
                    "appkey": app_key,
                    "appsecret": app_secret,
                },
            )
        )

    if res.status_code != 200:
        # 본문을 그대로 싣지 않습니다 — 보낸 키가 되돌아오는 경우가 있습니다.
        # 사람이 읽을 코드와 설명만 꺼냅니다 (EGW00133 = 1분에 한 번 제한).
        detail = ""
        try:
            body = res.json()
            code = str(body.get("error_code", "")).strip()
            text = str(body.get("error_description", "")).strip()
            if code or text:
                detail = f" [{code}] {text}"
        except ValueError:
            pass
        raise KisError(f"토큰 발급이 거절되었습니다 (HTTP {res.status_code}){detail}")

    body = res.json()
    token = body.get("access_token")
    if not token:
        raise KisError("응답에 access_token 이 없습니다")

    issued_at = datetime.now(timezone.utc)
    expires_in = int(body.get("expires_in", 0))
    return _Cached(
        token=token,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=expires_in),
    )


async def _ensure(mode: str, force: bool = False) -> tuple[_Cached, bool]:
    """쓸 수 있는 토큰을 캐시에 둔 뒤 (토큰, 캐시를 썼는지) 를 돌려줍니다."""
    if mode not in HOSTS:
        raise KisError("mode 는 paper 또는 live 여야 합니다")

    entry = _cache.get(mode)
    if not force and entry is not None and _fresh(entry, datetime.now(timezone.utc)):
        return entry, True

    async with _locks[mode]:
        # 자물쇠를 기다리는 동안 다른 요청이 이미 받아 놨을 수 있습니다. 한 번 더 봅니다
        entry = _cache.get(mode)
        if not force and entry is not None and _fresh(entry, datetime.now(timezone.utc)):
            return entry, True

        if not force:
            # 메모리에 없을 뿐, 재시작 전에 받아 둔 토큰이 살아 있을 수 있습니다
            saved = _load_from_disk(mode)
            if saved is not None and _fresh(saved, datetime.now(timezone.utc)):
                _cache[mode] = saved
                log.info("KIS %s 토큰을 저장해 둔 것에서 되살렸습니다", mode)
                return saved, True

        entry = await _request_new(mode)
        _cache[mode] = entry
        _save_to_disk(mode, entry)

    # 남기는 것은 만료 시각까지입니다. 토큰 값은 로그에 넣지 않습니다
    log.info("KIS %s 토큰을 새로 발급했습니다 (만료 %s)", mode, entry.expires_at.isoformat())
    return entry, False


def _describe(mode: str, entry: _Cached, cached: bool) -> TokenIssue:
    remaining = int((entry.expires_at - datetime.now(timezone.utc)).total_seconds())
    return TokenIssue(
        mode=mode,
        host=HOSTS[mode],
        issued_at=entry.issued_at,
        expires_at=entry.expires_at,
        expires_in=max(remaining, 0),
        token_preview=mask(entry.token),
        cached=cached,
    )


async def access_token(mode: str) -> str:
    """서버 안에서 TR 조회가 쓸 토큰. 이 문자열은 응답·로그로 내보내지 않습니다."""
    entry, _ = await _ensure(mode)
    return entry.token


async def request_access_token(mode: str, force: bool = False) -> TokenIssue:
    """화면용. 캐시가 살아 있으면 발급 요청 없이 그 상태를 그대로 돌려줍니다."""
    entry, cached = await _ensure(mode, force)
    return _describe(mode, entry, cached)


def reset_cache() -> None:
    """테스트에서 메모리 캐시를 비웁니다.

    파일은 건드리지 않습니다 — 테스트는 TOKEN_DIR 을 임시 폴더로 바꿔 쓰기 때문에,
    여기서 지우면 돌고 있는 서버가 들고 있던 진짜 토큰을 날리게 됩니다.
    """
    _cache.clear()


# ──────────────────────────────────────────────────────────────
# 거래일 캘린더 (실전 도메인 전용)
# ──────────────────────────────────────────────────────────────
def parse_calendar_rows(rows: list[dict[str, str]]) -> list[TradingDay]:
    """KIS 의 output 을 거래일로 바꿉니다. 날짜가 8자리가 아니면 버립니다."""
    days: list[TradingDay] = []

    for row in rows:
        raw = (row.get("bass_dt") or "").strip()
        if len(raw) != 8:
            continue

        days.append(
            TradingDay(
                date=date(int(raw[:4]), int(raw[4:6]), int(raw[6:])),
                is_open=row.get("opnd_yn") == "Y",
            )
        )

    return days


async def _calendar_page(
    client: httpx.AsyncClient, token: str, start: date, ctx_nk: str, ctx_fk: str, cont: str
) -> tuple[list[dict[str, str]], str, str, bool]:
    """한 페이지(24건)를 받아 (행, 다음 nk, 다음 fk, 더있음) 을 돌려줍니다."""
    app_key, app_secret = credentials(CALENDAR_MODE)

    res = await _guarded(
        client.get(
            f"{HOSTS[CALENDAR_MODE]}{CALENDAR_PATH}",
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": CALENDAR_TR_ID,
                "custtype": "P",
                "tr_cont": cont,
            },
            params={
                "BASS_DT": start.strftime("%Y%m%d"),
                "CTX_AREA_NK": ctx_nk,
                "CTX_AREA_FK": ctx_fk,
            },
        )
    )

    if res.status_code != 200:
        raise KisError(f"거래일 조회가 거절되었습니다 (HTTP {res.status_code})")

    body = res.json()
    if body.get("rt_cd") != "0":
        raise KisError(f"거래일 조회 실패 [{body.get('msg_cd')}] {body.get('msg1', '').strip()}")

    has_more = res.headers.get("tr_cont") in ("M", "F")
    return body.get("output") or [], body.get("ctx_area_nk", ""), body.get("ctx_area_fk", ""), has_more


async def fetch_trading_calendar(start: date, end: date) -> list[TradingDay]:
    """start~end 구간의 거래일을 받습니다. 이 TR 은 실전 도메인만 지원합니다.

    한 페이지가 고정으로 24건씩 오고, start 부터 이어서 미래로만 나갑니다 —
    날짜 구간을 끊어 거슬러 가는 일봉 조회와 다르게, 여기는 앞에서부터 쭉 읽습니다.
    """
    token = await access_token(CALENDAR_MODE)
    collected: dict[date, TradingDay] = {}
    ctx_nk, ctx_fk, cont = "", "", ""

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            rows, ctx_nk, ctx_fk, has_more = await _calendar_page(
                client, token, start, ctx_nk, ctx_fk, cont
            )
            cont = "N"

            for day in parse_calendar_rows(rows):
                collected[day.date] = day

            last_seen = max((d.date for d in parse_calendar_rows(rows)), default=None)
            if last_seen is None or last_seen >= end or not has_more:
                break

            await asyncio.sleep(RATE_SLEEP_SECONDS)

    return [collected[key] for key in sorted(collected) if start <= key <= end]


# ──────────────────────────────────────────────────────────────
# 일봉
# ──────────────────────────────────────────────────────────────
def parse_chart_rows(rows: list[dict[str, str]]) -> list[Candle]:
    """KIS 의 output2 를 일봉으로 바꿉니다.

    값이 전부 문자열로 오고, 날짜가 빈 줄이 섞여 옵니다. 그 줄은 버립니다.
    네트워크를 타지 않는 순수 함수라 테스트는 저장해 둔 응답으로 합니다.
    """
    candles: list[Candle] = []

    for row in rows:
        day = (row.get("stck_bsop_date") or "").strip()
        if len(day) != 8:
            continue

        candles.append(
            Candle(
                time=datetime.combine(
                    date(int(day[:4]), int(day[4:6]), int(day[6:])), time.min, tzinfo=KST
                ),
                open=float(row["stck_oprc"]),
                high=float(row["stck_hgpr"]),
                low=float(row["stck_lwpr"]),
                close=float(row["stck_clpr"]),
                volume=int(row["acml_vol"]),
            )
        )

    return candles


async def _chart_page(
    client: httpx.AsyncClient,
    mode: str,
    token: str,
    ticker: str,
    start: date,
    end: date,
) -> list[dict[str, str]]:
    """날짜 구간 하나(최대 100건)를 받아 output2 를 그대로 돌려줍니다."""
    app_key, app_secret = credentials(mode)

    res = await _guarded(
        client.get(
            f"{HOSTS[mode]}{CHART_PATH}",
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": CHART_TR_ID,
                "custtype": "P",
            },
            params={
                "FID_COND_MRKT_DIV_CODE": "J",  # 주식
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",  # 일봉
                "FID_ORG_ADJ_PRC": "0",  # 수정주가
            },
        )
    )

    if res.status_code != 200:
        # 본문에 보낸 헤더가 되돌아오는 경우가 있어 본문을 싣지 않습니다
        raise KisError(f"일봉 조회가 거절되었습니다 (HTTP {res.status_code})")

    body = res.json()
    if body.get("rt_cd") != "0":
        # msg1 은 KIS 가 주는 사람이 읽을 설명입니다. 키는 들어 있지 않습니다
        raise KisError(f"일봉 조회 실패 [{body.get('msg_cd')}] {body.get('msg1')}")

    return body.get("output2") or []


async def fetch_daily_candles(
    ticker: str, mode: str, start: date, end: date
) -> list[Candle]:
    """start~end 구간의 일봉을 전부 모아 날짜 오름차순으로 돌려줍니다.

    토큰은 access_token() 을 거칩니다 — 즉 캐시가 살아 있으면 발급 요청이 없습니다.
    """
    if mode not in HOSTS:
        raise KisError("mode 는 paper 또는 live 여야 합니다")
    if start > end:
        raise KisError("시작일이 종료일보다 늦습니다")

    token = await access_token(mode)
    collected: dict[datetime, Candle] = {}
    cursor = end

    async with httpx.AsyncClient(timeout=15.0) as client:
        while cursor >= start:
            page_start = max(start, cursor - timedelta(days=CHART_PAGE_DAYS))
            candles = parse_chart_rows(
                await _chart_page(client, mode, token, ticker, page_start, cursor)
            )

            if not candles:
                # 더 거슬러 올라가도 없다고 보고 멈춥니다 (상장 전 구간)
                break

            for candle in candles:
                collected[candle.time] = candle

            oldest = min(candle.time for candle in candles)
            next_cursor = oldest.date() - timedelta(days=1)
            if next_cursor >= cursor:
                # 더 못 내려가면 무한히 도는 것을 막습니다
                break
            cursor = next_cursor

            await asyncio.sleep(RATE_SLEEP_SECONDS)

    log.info("KIS %s 일봉 %s 건 조회 (%s)", mode, len(collected), ticker)
    return [collected[key] for key in sorted(collected)]
