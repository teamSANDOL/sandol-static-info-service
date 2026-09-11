"""셔틀 시간표 동기화 cron 진입점."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config.config import logger
from app.utils.shuttle_timetable import sync_shuttle_timetables

scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


async def poll_shuttle_timetable() -> None:
    """1회 동기화 틱. 셔틀 시간표는 학기당 몇 번만 바뀌므로 6시간 주기로 충분합니다."""
    logger.info("[shuttle_timetable_sync] synchronization tick")
    await sync_shuttle_timetables()


def start_scheduler() -> None:
    """6시간마다 도는 셔틀 시간표 동기화 잡을 시작합니다."""
    scheduler.add_job(
        poll_shuttle_timetable,
        trigger="cron",
        hour="*/6",
        minute=0,
        timezone="Asia/Seoul",
        id="shuttle_timetable_sync",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    """애플리케이션 종료 시 스케줄러를 정지합니다."""
    scheduler.shutdown()
