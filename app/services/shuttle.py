"""셔틀 시간표 조회 응답 조립과 백그라운드 동기화 예약."""

import asyncio
import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.config import Config
from app.schemas.shuttle import DayType
from app.utils.shuttle_timetable import (
    load_store,
    query_trips,
    sync_shuttle_timetables,
)

KST = ZoneInfo("Asia/Seoul")
_SOURCE_FIELDS = (
    "place",
    "title",
    "pdf_url",
    "parsed_at",
    "source",
    "updated_at",
)
_background: set[asyncio.Task[list[str]]] = set()  # create_task 결과가 GC되지 않게 보관


def build_timetable_response(  # noqa: PLR0913 - 쿼리 파라미터를 그대로 받는다
    route: str | None,
    day_type: DayType | None,
    origin: str | None,
    destination: str | None,
    date: datetime.date | None,
    include_all: bool,
) -> dict[str, object]:
    """저장된 시간표에서 출처 목록과 조건에 맞는 trip 목록을 만듭니다.

    Args:
        route: 노선명 부분 일치 필터.
        day_type: 운행일 구분 필터.
        origin: 출발지 필터(경유지 포함 일치).
        destination: 도착지 필터(경유지 포함 일치).
        date: 기준 날짜. None이면 오늘(KST).
        include_all: True면 운행 기간 필터를 쓰지 않는다.

    Returns:
        `sources`와 `trips`를 가진 응답 dict.

    Raises:
        HTTPException: 저장된 시간표가 없으면 404.
    """
    store = load_store()
    if not store:
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_FOUND,
            detail="파싱된 시간표가 아직 없습니다.",
        )
    query_date = date or datetime.datetime.now(KST).date()
    return {
        "sources": [{k: d.get(k) for k in _SOURCE_FIELDS} for d in store.values()],
        "trips": query_trips(
            store,
            route,
            day_type,
            origin,
            destination,
            query_date,
            include_all=include_all,
        ),
    }


def queue_sync(force: bool) -> None:
    """시간표 동기화를 백그라운드 태스크로 예약합니다.

    Args:
        force: True면 사용자 수정본과 PDF 해시 가드를 무시하고 재파싱한다.
    """
    task = asyncio.create_task(sync_shuttle_timetables(force))
    _background.add(task)
    task.add_done_callback(_background.discard)
