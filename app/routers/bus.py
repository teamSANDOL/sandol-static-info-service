"""셔틀버스 라우터.

- 이미지: GET /bus/images, GET /bus/image/{index} (iBook 뷰어 페이지 이미지)
- 시간표: GET /bus/timetable, POST /bus/timetable/refresh,
          PUT/DELETE /bus/timetable/{place}
- 쓰기(refresh/PUT/DELETE)는 Keycloak 관리자 Bearer 토큰이 필요합니다(require_admin).
"""

import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from app.config import Config
from app.routers.ibook_images import create_ibook_image_router
from app.schemas.shuttle import DayType, ParsedTimetable
from app.schemas.shuttle_responses import (
    DELETE_RESPONSES,
    PUT_RESPONSES,
    REFRESH_RESPONSES,
    TIMETABLE_RESPONSES,
)
from app.services import shuttle as service
from app.utils.audit import record_audit
from app.utils.keycloak_auth import require_admin
from app.utils.shuttle_timetable import delete_timetable, save_user_timetable

Admin = Annotated[str, Depends(require_admin)]

router = create_ibook_image_router("/bus", Config.SHUTTLE_URL, "버스")


@router.get("/timetable", responses=TIMETABLE_RESPONSES)
async def get_shuttle_timetable(
    route: str | None = None,
    day_type: DayType | None = None,
    origin: str | None = None,
    destination: str | None = None,
    date: datetime.date | None = None,
    include_all: bool = Query(False, alias="all"),
):
    """저장된 셔틀버스 시간표를 반환합니다. route는 부분 일치, origin/destination은 경유지 포함 일치.

    place는 출처 문서 이름일 뿐이며 한 PDF에 여러 노선이 있으므로 노선 필터는 route로 합니다.

    기본은 오늘(KST) 기준 운행 기간(valid_from/valid_to)에 해당하는 trip만 반환합니다.
    date 쿼리로 다른 날짜를 기준으로 조회하거나, all=true로 기간 필터 없이 전체를 조회할 수 있습니다.

    Args:
        route: 노선명 부분 일치 필터.
        day_type: 운행일 구분 필터.
        origin: 출발지 필터(경유지 포함).
        destination: 도착지 필터(경유지 포함).
        date: 기준 날짜(기본 오늘, KST).
        include_all: all=true면 운행 기간 필터를 쓰지 않는다.
    """
    return service.build_timetable_response(
        route, day_type, origin, destination, date, include_all
    )


@router.post(
    "/timetable/refresh",
    status_code=Config.HttpStatus.ACCEPTED,
    responses=REFRESH_RESPONSES,
)
async def refresh_shuttle_timetable(sub: Admin, force: bool = False):
    """수동으로 셔틀 시간표 동기화를 트리거합니다. 평소엔 6시간 cron이 대신합니다.

    PDF 파싱은 빠르지만 다운로드가 느릴 수 있어 백그라운드로 돌립니다.
    force=True면 사용자 수정본과 PDF 해시 가드를 모두 무시하고 재파싱합니다
    (사용자 수정본을 덮어쓰는 유일한 경로이며, 명시적으로 호출했을 때만 동작).

    Args:
        sub: 관리자 Keycloak sub(인증 의존성이 채운다).
        force: True면 사용자 수정본과 해시 가드를 무시한다.
    """
    record_audit(sub, "shuttle.timetable.refresh", f"force={force}")
    service.queue_sync(force)
    return {"queued": True}


@router.put("/timetable/{place}", responses=PUT_RESPONSES)
async def put_shuttle_timetable(place: str, body: ParsedTimetable, sub: Admin):
    """place의 시간표를 사용자가 직접 입력한 내용으로 덮어씁니다.

    저장 문서는 source="user"로 표시되어 cron/refresh(force 없이는)가 다시 파싱하지 않습니다.
    """
    doc = await save_user_timetable(place, body.trips, body.title, updated_by=sub)
    record_audit(sub, "shuttle.timetable.put", place)
    return doc


@router.delete(
    "/timetable/{place}",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses=DELETE_RESPONSES,
)
async def delete_shuttle_timetable(place: str, sub: Admin):
    """place 문서를 삭제합니다. 사용자 수정본을 되돌려 다시 PDF 파싱 대상으로 만들 때 사용합니다."""
    if not await delete_timetable(place):
        raise HTTPException(status_code=Config.HttpStatus.NOT_FOUND)
    record_audit(sub, "shuttle.timetable.delete", place)
