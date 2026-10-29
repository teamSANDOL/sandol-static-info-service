"""셔틀버스 라우터.

- 이미지: GET /bus/images, GET /bus/image/{index} (iBook 뷰어 페이지 이미지)
- 시간표: GET /bus/timetable, POST /bus/timetable/refresh,
          PUT/DELETE /bus/timetable/{place}
- 쓰기(refresh/PUT/DELETE)는 Keycloak Bearer 토큰과 셔틀 관리자 롤이 필요합니다
  (require_shuttle_admin, SHUTTLE_ADMIN_ROLES). 세밀한 편집은 shuttle_admin 라우터.
"""

import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Response

from app.config import Config
from app.routers.ibook_images import create_ibook_image_router
from app.schemas.shuttle import ParsedTimetable
from app.schemas.shuttle_responses import (
    DELETE_RESPONSES,
    PUT_RESPONSES,
    REFRESH_RESPONSES,
    TIMETABLE_RESPONSES,
)
from app.services import shuttle as service
from app.services.shuttle_admin import ensure_force_allowed
from app.services.shuttle_store import delete_timetable, save_user_timetable
from app.utils.audit import arecord_audit
from app.utils.keycloak_auth import require_shuttle_admin, shuttle_admin_identity

Admin = Annotated[str, Depends(require_shuttle_admin)]

router = create_ibook_image_router("/bus", Config.SHUTTLE_URL, "버스")


@router.get("/timetable", responses=TIMETABLE_RESPONSES)
async def get_shuttle_timetable(
    response: Response,
    date: datetime.date | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
):
    """조회일 하루치 셔틀 시간표를 모바일이 해석 없이 그릴 수 있는 형태로 반환합니다.

    응답에 `ETag: "<version>"`와 `Cache-Control: public, max-age=300`을 붙이고,
    `If-None-Match`가 현재 태그와 같으면 본문 없이 304를 돌려줍니다. date를 생략하면
    태그는 `"<version>-<YYYYMMDD(KST)>"`입니다.



    Args:
        response: 헤더를 붙일 응답.
        date: 조회일(기본 오늘, KST).
        if_none_match: 클라이언트가 가진 ETag 목록.
    """
    body, tag = service.public_timetable(date)
    headers = {"ETag": f'"{tag}"', "Cache-Control": service.CACHE_CONTROL}
    if service.etag_matches(if_none_match, tag):
        return Response(status_code=Config.HttpStatus.NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return body


@router.post(
    "/timetable/refresh",
    status_code=Config.HttpStatus.ACCEPTED,
    responses=REFRESH_RESPONSES,
)
async def refresh_shuttle_timetable(
    identity: Annotated[tuple[str, list[str]], Depends(shuttle_admin_identity)],
    force: bool = False,
):
    """수동으로 셔틀 시간표 동기화를 트리거합니다. 평소엔 6시간 cron이 대신합니다.

    PDF 파싱은 빠르지만 다운로드가 느릴 수 있어 백그라운드로 돌립니다.
    force=True면 사용자 수정본과 PDF 해시 가드를 모두 무시하고 재파싱합니다
    (사용자 수정본을 덮어쓰는 유일한 경로이며, 명시적으로 호출했을 때만 동작).



    Args:
        identity: (관리자 sub, 롤 목록). 인증 의존성이 채운다.
        force: True면 수정본까지 덮어쓴다(전체 관리자만).
    """
    sub, roles = identity
    # 수정본까지 덮어쓰는 force는 전체 관리자만. 셔틀 관리자는 일반 갱신만 한다.
    ensure_force_allowed(
        roles, force, "force 동기화(수정본 덮어쓰기)는 전체 관리자만 할 수 있습니다."
    )
    await arecord_audit(sub, "shuttle.timetable.refresh", f"force={force}")
    service.queue_sync(force)
    return {"queued": True}


@router.put("/timetable/{place}", responses=PUT_RESPONSES)
async def put_shuttle_timetable(place: str, body: ParsedTimetable, sub: Admin):
    """place의 시간표를 사용자가 직접 입력한 내용으로 덮어씁니다.

    저장 문서는 source="user"로 표시되어 cron/refresh(force 없이는)가 다시 파싱하지 않습니다.
    """
    doc = await save_user_timetable(
        place, body.trips, body.title, updated_by=sub, notes=body.notes
    )
    await arecord_audit(sub, "shuttle.timetable.put", place)
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
    await arecord_audit(sub, "shuttle.timetable.delete", place)
