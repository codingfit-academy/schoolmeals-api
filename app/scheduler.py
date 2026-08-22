"""
배치 스케줄러 — 평일 새벽 1시 NEIS 수집 실행
─────────────────────────────────────────────────────────────
Dockerfile이 `uvicorn --workers 2`로 앱을 띄우기 때문에, 아무 보호 장치 없이
각 워커에서 스케줄러를 그대로 띄우면 같은 시각에 배치가 두 번 실행된다.
Postgres 세션 어드바이저리 락(pg_try_advisory_lock)으로 감싸서, 여러 워커가
동시에 트리거돼도 실제로는 한쪽만 실행되고 나머지는 즉시 스킵하게 한다.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from .database import engine
from .services.ingest import run_daily_ingest

logger = logging.getLogger("scheduler")

# 이 배치 작업 전용으로 임의 고정한 advisory lock 키
_INGEST_LOCK_KEY = 875_321_001

_scheduler: AsyncIOScheduler | None = None


async def _run_ingest_with_lock() -> None:
    async with engine.connect() as conn:
        got_lock = (
            await conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": _INGEST_LOCK_KEY}
            )
        ).scalar()

        if not got_lock:
            logger.info("다른 워커가 이미 배치를 실행 중 — 이번 트리거는 스킵")
            return

        try:
            await run_daily_ingest()
        except Exception:
            logger.exception("배치 수집 중 예외 발생")
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _INGEST_LOCK_KEY}
            )


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    _scheduler.add_job(
        _run_ingest_with_lock,
        CronTrigger(day_of_week="mon-fri", hour=1, minute=0),
        id="neis_daily_ingest",
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info("스케줄러 시작 — 평일 01:00(Asia/Seoul)에 배치 수집 실행")
    return _scheduler


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
