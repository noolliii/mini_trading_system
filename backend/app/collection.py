"""증분 수집의 실제 로직. API 엔드포인트와 스케줄러가 똑같이 이걸 거칩니다.

한 군데에만 있어야, "버튼으로 받을 때"와 "새벽에 자동으로 받을 때"가
서로 다르게 동작하는 일이 없습니다.
"""

import logging
from datetime import datetime, timedelta

from . import db, kis, trading_calendar
from .schemas import CollectResult

log = logging.getLogger(__name__)

DEFAULT_FULL_DAYS = 365


class TickerNotFound(Exception):
    pass


async def collect_incremental_ticker(ticker: str, mode: str) -> CollectResult:
    """한 종목만. 마지막 적재일 다음 거래일부터 오늘까지만 받습니다."""
    async with db.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM stocks WHERE ticker = $1", ticker)
        if exists is None:
            raise TickerNotFound(ticker)

        last = await conn.fetchval(
            "SELECT max(time)::date FROM daily_candles WHERE ticker = $1", ticker
        )
        open_days = [
            r["date"]
            for r in await conn.fetch(
                "SELECT date FROM trading_days WHERE is_open AND date > coalesce($1, date - 1)",
                last,
            )
        ]

    today = datetime.now(kis.KST).date()

    if last is None:
        # 한 번도 받은 적이 없으면 처음 받을 때와 같은 기본 구간으로 전부 받습니다
        start = today - timedelta(days=DEFAULT_FULL_DAYS)
    else:
        start = trading_calendar.next_trading_day_after(open_days, last)
        if start is None:
            # 캘린더가 이 구간을 모릅니다 — 하루 뒤로 물러서서라도 진행합니다
            start = last + timedelta(days=1)

    if start > today:
        return CollectResult(ticker=ticker, mode=mode, fetched=0, inserted=0)

    fetched = await kis.fetch_daily_candles(ticker, mode, start, today)

    if not fetched:
        return CollectResult(ticker=ticker, mode=mode, fetched=0, inserted=0)

    async with db.acquire() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM daily_candles WHERE ticker = $1", ticker
        )
        await conn.executemany(
            """
            INSERT INTO daily_candles (time, ticker, open, high, low, close, volume)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (ticker, time) DO UPDATE
               SET open = excluded.open, high = excluded.high, low = excluded.low,
                   close = excluded.close, volume = excluded.volume
            """,
            [(c.time, ticker, c.open, c.high, c.low, c.close, c.volume) for c in fetched],
        )
        after = await conn.fetchval(
            "SELECT count(*) FROM daily_candles WHERE ticker = $1", ticker
        )

    return CollectResult(
        ticker=ticker,
        mode=mode,
        fetched=len(fetched),
        inserted=after - before,
        first=fetched[0].time,
        last=fetched[-1].time,
    )


async def _is_today_a_trading_day() -> bool | None:
    """오늘 자체가 거래일인지 캘린더로 봅니다. 캘린더가 모르면 None (모름)."""
    today = datetime.now(kis.KST).date()
    async with db.acquire() as conn:
        row = await conn.fetchval(
            "SELECT is_open FROM trading_days WHERE date = $1", today
        )
    return row


async def run_incremental_for_all_stocks(mode: str = "paper") -> dict[str, object]:
    """종목 전체를 증분 수집하고, 신선도를 남기고, Discord 로 요약을 보냅니다.

    cron 을 mon-fri 로만 걸어도 공휴일엔 여전히 돕니다 — 그래서 여기서
    거래일 캘린더로 한 번 더 걸러, 휴장일이면 아무 종목도 건드리지 않고 건너뜁니다.
    """
    from . import discord  # 늦게 import — 순환 참조를 피하려고

    today_open = await _is_today_a_trading_day()
    if today_open is False:
        log.info("오늘은 휴장일이라 증분 수집을 건너뜁니다")
        return {"skipped_holiday": True, "success": 0, "failed": 0, "tickers": 0}

    async with db.acquire() as conn:
        tickers = [r["ticker"] for r in await conn.fetch("SELECT ticker FROM stocks ORDER BY ticker")]

    success = 0
    failed = 0
    details: list[str] = []

    for ticker in tickers:
        try:
            result = await collect_incremental_ticker(ticker, mode)
        except TickerNotFound:
            # 수집 도중 종목이 지워진 경우입니다. 있을 수 있는 일이라 조용히 넘어갑니다
            continue
        except Exception as e:  # noqa: BLE001 — 한 종목의 어떤 실패든 나머지를 멈추지 않습니다
            # KisError 뿐 아니라 네트워크 타임아웃·예상 못한 버그까지 여기서 받습니다.
            # 무인으로 도는 스케줄러에서 한 종목이 터져 나머지를 못 받으면 더 나쁩니다
            failed += 1
            details.append(f"{ticker}: 실패 — {e}")
            log.exception("증분 수집 중 %s 에서 예외", ticker)
            await _save_status(ticker, None, "failed", str(e))
            continue

        success += 1
        note = "새 거래일 없음" if result.fetched == 0 else f"{result.fetched}건"
        details.append(f"{ticker}: {note}")
        last_date = result.last.date() if result.last else None
        await _save_status(ticker, last_date, "success", None)

    log.info("증분 수집 끝 — 성공 %d · 실패 %d (종목 %d개)", success, failed, len(tickers))
    await discord.send_summary(success, failed, 0, details)

    return {"skipped_holiday": False, "success": success, "failed": failed, "tickers": len(tickers)}


async def _save_status(ticker: str, last_date, status: str, message: str | None) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO collection_status (ticker, last_date, checked_at, status, message)
            VALUES ($1, $2, now(), $3, $4)
            ON CONFLICT (ticker) DO UPDATE
               SET last_date = excluded.last_date, checked_at = excluded.checked_at,
                   status = excluded.status, message = excluded.message
            """,
            ticker, last_date, status, message,
        )
