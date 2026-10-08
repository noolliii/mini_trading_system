"""장 마감 뒤 증분 수집을 자동으로 돌립니다. FastAPI lifespan 에서 켜고 끕니다.

cron 을 "월~금" 으로만 걸면 공휴일에도 돕니다 — 그 거르기는 cron 몫이 아니라
collection.run_incremental_for_all_stocks 안에서 거래일 캘린더로 한 번 더 합니다.
"""

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import collection

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 국내 증시 정규장은 15:30 마감입니다. 30분 여유를 두고 받습니다.
# 컨테이너의 시스템 시간대가 달라도 맞게 돌도록 트리거에 시간대를 직접 박아 둡니다.
MARKET_CLOSE_TRIGGER = CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=KST)

_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    try:
        await collection.run_incremental_for_all_stocks()
    except Exception:  # noqa: BLE001 — 스케줄러 잡이 죽으면 다음 날 것도 안 돕니다
        log.exception("증분 수집 잡이 실패했습니다")


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone=KST)
    _scheduler.add_job(_job, MARKET_CLOSE_TRIGGER, id="incremental_collection")
    _scheduler.start()
    log.info("증분 수집 스케줄러를 켰습니다 (평일 16:00 KST)")


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
