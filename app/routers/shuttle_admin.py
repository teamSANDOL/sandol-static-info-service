"""셔틀 시간표 관리자 API(/bus/admin/timetables).

셔틀 관리자가 문서(place)와 운행편을 직접 만들고·고치고·지우며, 검증에 걸린 새 PDF
파싱본을 승인·거부합니다. 모든 경로는 Keycloak Bearer 토큰과 SHUTTLE_ADMIN_ROLES
(기본 global_admin, shuttle_admin) 중 하나가 필요하고, 쓰기는 감사 로그를 남깁니다.
바뀐 내용은 저장 즉시 공개 GET /bus/timetable 에 반영되고 version도 바뀝니다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.config import Config
from app.schemas.responses import SHUTTLE_AUTH_RESPONSES, OpenApiResponses
from app.schemas.shuttle import (
    AdminTimetableCreate,
    AdminTimetableDetail,
    AdminTimetablePatch,
    AdminTimetableSummary,
    AdminTripIn,
    AdminTripOut,
)
from app.services.shuttle_admin import (
    add_document_trip,
    admin_detail,
    admin_summary,
    admin_trip,
    ensure_force_allowed,
    missing,
    patch_document,
    remove_document,
    replace_document_trip,
    review_document,
)
from app.services.shuttle_store import create_timetable, load_store, stored_trip
from app.utils.audit import arecord_audit
from app.utils.keycloak_auth import require_shuttle_admin, shuttle_admin_identity

ShuttleAdmin = Annotated[str, Depends(require_shuttle_admin)]
IfMatch = Annotated[
    str | None,
    Header(description="문서 revision. 다르면 412(다른 사람이 먼저 고침), 생략 가능"),
]
_NOT_FOUND: dict[str, str] = {"description": "해당 place 문서(또는 운행편)가 없습니다."}
_REVIEW_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.NOT_FOUND: _NOT_FOUND,
    Config.HttpStatus.CONFLICT: {"description": "검토 대기가 없습니다."},
}

router = APIRouter(
    prefix="/bus/admin/timetables",
    tags=["Shuttle Admin"],
    responses=SHUTTLE_AUTH_RESPONSES,
)


@router.get("", response_model=list[AdminTimetableSummary])
async def list_timetables(sub: ShuttleAdmin) -> list[dict]:
    """저장된 문서 목록(연도 포함 유효기간·편수·검토 대기 여부)."""
    docs = sorted(load_store().values(), key=lambda d: d["place"])
    return [admin_summary(d) for d in docs]


@router.post(
    "",
    status_code=Config.HttpStatus.CREATED,
    response_model=AdminTimetableDetail,
    responses={
        Config.HttpStatus.CONFLICT: {"description": "같은 place가 이미 있습니다."}
    },
)
async def create_doc(body: AdminTimetableCreate, sub: ShuttleAdmin) -> dict:
    """새 문서를 만듭니다(source=user). 유효기간을 비우면 상시 문서입니다."""
    doc = await create_timetable(body.model_dump(), sub)
    if doc is None:
        raise HTTPException(Config.HttpStatus.CONFLICT, "같은 place가 이미 있습니다.")
    await arecord_audit(sub, "shuttle.document.create", body.place)
    return admin_detail(doc)


@router.get(
    "/{place}",
    response_model=AdminTimetableDetail,
    responses={Config.HttpStatus.NOT_FOUND: _NOT_FOUND},
)
async def get_detail(place: str, sub: ShuttleAdmin) -> dict:
    """문서 상세. 운행편마다 안정 id가 붙습니다."""
    doc = load_store().get(place)
    if doc is None:
        raise missing()
    return admin_detail(doc)


@router.patch(
    "/{place}",
    response_model=AdminTimetableDetail,
    responses={
        Config.HttpStatus.NOT_FOUND: _NOT_FOUND,
        Config.HttpStatus.UNPROCESSABLE_ENTITY: {
            "description": "유효기간이 맞지 않습니다."
        },
    },
)
async def patch_meta(
    place: str, body: AdminTimetablePatch, sub: ShuttleAdmin, if_match: IfMatch = None
) -> dict:
    """제목·유효기간·안내문을 고칩니다. 보낸 필드만 바뀝니다."""
    fields = body.model_dump(exclude_unset=True)
    doc = await patch_document(place, fields, sub, if_match)
    await arecord_audit(
        sub, "shuttle.document.update", f"{place} fields={sorted(fields)}"
    )
    return admin_detail(doc)


@router.delete(
    "/{place}",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={Config.HttpStatus.NOT_FOUND: _NOT_FOUND},
)
async def delete_doc(
    place: str, sub: ShuttleAdmin, if_match: IfMatch = None
) -> Response:
    """문서를 지웁니다. PDF 문서였다면 다음 동기화 때 다시 파싱됩니다."""
    await remove_document(place, if_match)
    await arecord_audit(sub, "shuttle.document.delete", place)
    return Response(status_code=Config.HttpStatus.NO_CONTENT)


@router.post(
    "/{place}/trips",
    status_code=Config.HttpStatus.CREATED,
    response_model=AdminTripOut,
    responses={Config.HttpStatus.NOT_FOUND: _NOT_FOUND},
)
async def add_trip(
    place: str, body: AdminTripIn, sub: ShuttleAdmin, if_match: IfMatch = None
) -> dict:
    """운행편을 추가합니다. PDF 문서면 사용자 수정본이 됩니다."""
    trip = await add_document_trip(place, body.model_dump(), sub, if_match)
    await arecord_audit(sub, "shuttle.trip.create", f"{place} id={trip['id']}")
    return admin_trip(trip)


@router.put(
    "/{place}/trips/{trip_id}",
    response_model=AdminTripOut,
    responses={Config.HttpStatus.NOT_FOUND: _NOT_FOUND},
)
async def update_trip(
    place: str,
    trip_id: str,
    body: AdminTripIn,
    sub: ShuttleAdmin,
    if_match: IfMatch = None,
) -> dict:
    """운행편 하나를 통째로 바꿉니다. id는 그대로입니다."""
    trip = stored_trip(body.model_dump(), trip_id)
    await replace_document_trip(place, trip_id, trip, sub, if_match)
    await arecord_audit(sub, "shuttle.trip.update", f"{place} id={trip_id}")
    return admin_trip(trip)


@router.delete(
    "/{place}/trips/{trip_id}",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={Config.HttpStatus.NOT_FOUND: _NOT_FOUND},
)
async def delete_trip(
    place: str, trip_id: str, sub: ShuttleAdmin, if_match: IfMatch = None
) -> Response:
    """운행편 하나를 지웁니다. 입력 규칙에 어긋난 옛 편도 id로 지울 수 있습니다."""
    await replace_document_trip(place, trip_id, None, sub, if_match)
    await arecord_audit(sub, "shuttle.trip.delete", f"{place} id={trip_id}")
    return Response(status_code=Config.HttpStatus.NO_CONTENT)


@router.post(
    "/{place}/review/approve",
    response_model=AdminTimetableDetail,
    responses=_REVIEW_RESPONSES,
)
async def approve(
    place: str,
    identity: Annotated[tuple[str, list[str]], Depends(shuttle_admin_identity)],
    force: bool = False,
) -> dict:
    """검토 대기 파싱본을 정상본으로 승격합니다(needs_review 해제).

    검토 대기본이 생긴 뒤 문서를 고쳤다면 수정 내용이 사라지므로 409이며,
    force=true일 때만 파싱본으로 덮어씁니다. 수정본을 덮어쓰는 force는 refresh
    force와 같이 전체 관리자(KC_ADMIN_ROLE)만 할 수 있습니다.



    Args:
        place: 문서 이름.
        identity: (관리자 sub, 롤 목록). 인증 의존성이 채운다.
        force: True면 수정본을 파싱본으로 덮어쓴다.
    """
    sub, roles = identity
    ensure_force_allowed(
        roles, force, "force 승인(수정본 덮어쓰기)은 전체 관리자만 할 수 있습니다."
    )
    doc = await review_document(place, True, sub, force)
    await arecord_audit(sub, "shuttle.pending.approve", place)
    return admin_detail(doc)


@router.post(
    "/{place}/review/reject",
    response_model=AdminTimetableDetail,
    responses=_REVIEW_RESPONSES,
)
async def reject(place: str, sub: ShuttleAdmin) -> dict:
    """검토 대기 파싱본을 버립니다. 같은 PDF는 다시 파싱하지 않습니다."""
    doc = await review_document(place, False, sub)
    await arecord_audit(sub, "shuttle.pending.reject", place)
    return admin_detail(doc)
