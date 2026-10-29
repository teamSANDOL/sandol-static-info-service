"""공개 셔틀 시간표 응답(ETag 포함)과 백그라운드 동기화 예약."""

import asyncio
import datetime

from fastapi import HTTPException

from app.config import Config
from app.services.shuttle_schedule import (
    KST,
    build_timetable,
    needs_review,
    pick_document,
)
from app.services.shuttle_store import load_store
from app.services.shuttle_sync import sync_shuttle_timetables

CACHE_CONTROL = "public, max-age=300"
_background: set[asyncio.Task[list[str]]] = set()  # create_task 결과가 GC되지 않게 보관


def public_timetable(date: datetime.date | None) -> tuple[dict, str]:
    """조회일 하루치 공개 시간표와 그 ETag 태그를 만듭니다.

    version은 날짜와 무관하므로 date 없이(=오늘) 부르면 날짜를 태그에 섞어야 다음 날
    재검증에서 어제 본문이 304로 남지 않습니다. 앱은 date를 항상 보냅니다.

    Args:
        date: 조회일. None이면 오늘(KST).

    Returns:
        (응답 본문, 따옴표 없는 ETag 태그).

    Raises:
        HTTPException: 저장된 시간표가 아예 없으면 503(timetable_unavailable).
    """
    store = load_store()
    if not store:
        raise HTTPException(
            status_code=Config.HttpStatus.SERVICE_UNAVAILABLE,
            detail="timetable_unavailable",
        )
    query_date = date or datetime.datetime.now(KST).date()
    doc = pick_document(store, query_date)
    body = build_timetable(doc, query_date, needs_review=needs_review(doc))
    tag = body["version"] if date else f"{body['version']}-{query_date:%Y%m%d}"
    return body, tag


def etag_matches(if_none_match: str | None, version: str) -> bool:
    """If-None-Match(쉼표 목록, W/ 약한 태그, *)에 현재 version이 있는지.

    Args:
        if_none_match: 요청 헤더 값.
        version: 현재 ETag 태그(따옴표 없음).

    Returns:
        일치하면 True.
    """
    tags = {t.strip().removeprefix("W/") for t in (if_none_match or "").split(",")}
    return "*" in tags or f'"{version}"' in tags


def queue_sync(force: bool) -> None:
    """시간표 동기화를 백그라운드 태스크로 예약합니다.

    Args:
        force: True면 사용자 수정본과 PDF 해시 가드를 무시하고 재파싱한다.
    """
    task = asyncio.create_task(sync_shuttle_timetables(force))
    _background.add(task)
    task.add_done_callback(_background.discard)
