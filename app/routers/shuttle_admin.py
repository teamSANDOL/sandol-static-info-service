"""셔틀 시간표 관리자 API(/bus/admin/timetables).

셔틀 관리자가 문서(place)와 운행편을 직접 만들고·고치고·지우며, 검증에 걸린 새 PDF
파싱본을 승인·거부합니다. 모든 경로는 Keycloak Bearer 토큰과 SHUTTLE_ADMIN_ROLES
(기본 global_admin, shuttle_admin) 중 하나가 필요하고, 쓰기는 감사 로그를 남깁니다.
바뀐 내용은 저장 즉시 공개 GET /bus/timetable 에 반영되고 version도 바뀝니다.
"""

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.config import Config, logger
from app.schemas.shuttle import (
    AdminTimetableCreate,
    AdminTimetableDetail,
    AdminTimetablePatch,
    AdminTimetableSummary,
    AdminTripIn,
    AdminTripOut,
)
from app.utils.keycloak_auth import require_shuttle_admin, shuttle_admin_identity
from app.utils.shuttle_timetable import (
    admin_detail,
    admin_summary,
    admin_trip,
    create_timetable,
    delete_timetable,
    edit_timetable,
    load_store,
    RevisionConflictError,
    review_timetable,
    stored_trip,
)

ShuttleAdmin = Annotated[str, Depends(require_shuttle_admin)]
IfMatch = Annotated[
    str | None,
    Header(description="문서 revision. 다르면 412(다른 사람이 먼저 고침), 생략 가능"),
]
_NOT_FOUND = {"description": "해당 place 문서(또는 운행편)가 없습니다."}
_AUTH = {
    401: {"description": "Bearer 토큰이 없거나 유효하지 않습니다."},
    403: {"description": "셔틀 관리자 롤이 없습니다."},
    503: {"description": "관리자 인증이 설정되지 않았습니다(fail-closed)."},
}

router = APIRouter(
    prefix="/bus/admin/timetables", tags=["셔틀 시간표 관리자"], responses=_AUTH
)


def _audit(sub: str, action: str, target: str) -> None:
    # target엔 place 등 사용자 입력이 섞여 줄바꿈·따옴표를 이스케이프해 로그 위조를 막는다
    # (sub는 서명 검증된 토큰 값).
    logger.info(
        "admin_audit sub=%s action=%s target=%s",
        sub,
        action,
        json.dumps(target, ensure_ascii=False),
    )


def _missing(what: str = "문서") -> HTTPException:
    return HTTPException(Config.HttpStatus.NOT_FOUND, f"{what}가 없습니다.")


@router.get("", response_model=list[AdminTimetableSummary])
async def list_timetables(sub: ShuttleAdmin) -> list[dict]:
    """저장된 문서 목록(연도 포함 유효기간·편수·검토 대기 여부)."""
    docs = sorted(load_store().values(), key=lambda d: d["place"])
    return [admin_summary(d) for d in docs]


@router.post(
    "",
    status_code=Config.HttpStatus.CREATED,
    response_model=AdminTimetableDetail,
    responses={409: {"description": "같은 place가 이미 있습니다."}},
)
async def create_doc(body: AdminTimetableCreate, sub: ShuttleAdmin) -> dict:
    """새 문서를 만듭니다(source=user). 유효기간을 비우면 상시 문서입니다."""
    doc = await create_timetable(body.model_dump(), sub)
    if doc is None:
        raise HTTPException(Config.HttpStatus.CONFLICT, "같은 place가 이미 있습니다.")
    _audit(sub, "shuttle_admin.create", body.place)
    return admin_detail(doc)


@router.get(
    "/{place}", response_model=AdminTimetableDetail, responses={404: _NOT_FOUND}
)
async def get_detail(place: str, sub: ShuttleAdmin) -> dict:
    """문서 상세. 운행편마다 안정 id가 붙습니다."""
    doc = load_store().get(place)
    if doc is None:
        raise _missing()
    return admin_detail(doc)


@router.patch(
    "/{place}",
    response_model=AdminTimetableDetail,
    responses={404: _NOT_FOUND, 422: {"description": "유효기간이 맞지 않습니다."}},
)
async def patch_meta(
    place: str, body: AdminTimetablePatch, sub: ShuttleAdmin, if_match: IfMatch = None
) -> dict:
    """제목·유효기간·안내문을 고칩니다. 보낸 필드만 바뀝니다."""
    fields = body.model_dump(exclude_unset=True)
    if ("valid_from" in fields) != ("valid_to" in fields):
        raise HTTPException(
            Config.HttpStatus.UNPROCESSABLE_ENTITY,
            "valid_from과 valid_to는 함께 보냅니다(둘 다 null이면 상시).",
        )
    vf, vt = fields.get("valid_from"), fields.get("valid_to")
    if (vf is None) != (vt is None) or (vf and vt and vf > vt):
        raise HTTPException(
            Config.HttpStatus.UNPROCESSABLE_ENTITY, "유효기간이 올바르지 않습니다."
        )

    def change(doc: dict) -> None:
        for key in ("title", "notes"):
            if key in fields:
                doc[key] = fields[key] if key == "title" else fields[key] or []
        if "valid_from" in fields:
            doc["valid_from"] = vf.isoformat() if vf else None
            doc["valid_to"] = vt.isoformat() if vt else None
            if vf:
                doc["ref_date"] = vf.isoformat()

    doc = await _edit(place, change, sub, if_match)
    if doc is None:
        raise _missing()
    _audit(sub, "shuttle_admin.patch", f"{place} fields={sorted(fields)}")
    return admin_detail(doc)


@router.delete(
    "/{place}", status_code=Config.HttpStatus.NO_CONTENT, responses={404: _NOT_FOUND}
)
async def delete_doc(
    place: str, sub: ShuttleAdmin, if_match: IfMatch = None
) -> Response:
    """문서를 지웁니다. PDF 문서였다면 다음 동기화 때 다시 파싱됩니다."""
    try:
        deleted = await delete_timetable(place, if_match=_tag(if_match))
    except RevisionConflictError as exc:
        raise HTTPException(
            Config.HttpStatus.PRECONDITION_FAILED,
            "다른 사람이 먼저 고쳤습니다. 상세를 다시 읽고 revision을 확인하세요.",
        ) from exc
    if not deleted:
        raise _missing()
    _audit(sub, "shuttle_admin.delete", place)
    return Response(status_code=Config.HttpStatus.NO_CONTENT)


@router.post(
    "/{place}/trips",
    status_code=Config.HttpStatus.CREATED,
    response_model=AdminTripOut,
    responses={404: _NOT_FOUND},
)
async def add_trip(
    place: str, body: AdminTripIn, sub: ShuttleAdmin, if_match: IfMatch = None
) -> dict:
    """운행편을 추가합니다. PDF 문서면 사용자 수정본이 됩니다."""
    trip = stored_trip(body.model_dump())
    doc = await _edit(place, lambda d: d["trips"].append(trip), sub, if_match)
    if doc is None:
        raise _missing()
    _audit(sub, "shuttle_admin.trip_add", f"{place} id={trip['id']}")
    return admin_trip(trip)


def _replace(trip_id: str, new: dict | None) -> Callable[[dict], None]:
    """id가 trip_id인 운행편을 new로 바꾸거나(None이면) 지우는 변경. 없으면 KeyError."""

    def change(doc: dict) -> None:
        idx = next((i for i, t in enumerate(doc["trips"]) if t["id"] == trip_id), None)
        if idx is None:
            raise KeyError(trip_id)
        if new is None:
            del doc["trips"][idx]
        else:
            doc["trips"][idx] = new

    return change


def _tag(if_match: str | None) -> str | None:
    """If-Match 값 → 비교할 revision. 없거나 '*'(존재만 확인)면 None."""
    tag = (if_match or "").strip().removeprefix("W/").strip('"')
    return None if tag in ("", "*") else tag


async def _edit(
    place: str, change: Callable[[dict], None], sub: str, if_match: str | None
) -> dict | None:
    """edit_timetable + If-Match(수정 버전) 충돌을 412로."""
    try:
        return await edit_timetable(place, change, sub, if_match=_tag(if_match))
    except RevisionConflictError as exc:
        raise HTTPException(
            Config.HttpStatus.PRECONDITION_FAILED,
            "다른 사람이 먼저 고쳤습니다. 상세를 다시 읽고 revision을 확인하세요.",
        ) from exc


async def _edit_trip(
    place: str, trip_id: str, new: dict | None, sub: str, if_match: str | None = None
) -> None:
    try:
        doc = await _edit(place, _replace(trip_id, new), sub, if_match)
    except KeyError as exc:
        raise _missing("운행편") from exc
    if doc is None:
        raise _missing()


@router.put(
    "/{place}/trips/{trip_id}", response_model=AdminTripOut, responses={404: _NOT_FOUND}
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
    await _edit_trip(place, trip_id, trip, sub, if_match)
    _audit(sub, "shuttle_admin.trip_update", f"{place} id={trip_id}")
    return admin_trip(trip)


@router.delete(
    "/{place}/trips/{trip_id}",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={404: _NOT_FOUND},
)
async def delete_trip(
    place: str, trip_id: str, sub: ShuttleAdmin, if_match: IfMatch = None
) -> Response:
    """운행편 하나를 지웁니다. 입력 규칙에 어긋난 옛 편도 id로 지울 수 있습니다."""
    await _edit_trip(place, trip_id, None, sub, if_match)
    _audit(sub, "shuttle_admin.trip_delete", f"{place} id={trip_id}")
    return Response(status_code=Config.HttpStatus.NO_CONTENT)


async def _review(place: str, approve: bool, sub: str, force: bool = False) -> dict:
    try:
        doc = await review_timetable(place, approve, sub, force)
    except ValueError as exc:
        raise HTTPException(Config.HttpStatus.CONFLICT, str(exc)) from exc
    if doc is None:
        raise _missing()
    _audit(sub, f"shuttle_admin.review_{'approve' if approve else 'reject'}", place)
    return admin_detail(doc)


_REVIEW_RESPONSES = {404: _NOT_FOUND, 409: {"description": "검토 대기가 없습니다."}}


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
    """
    sub, roles = identity
    if force and Config.KC_ADMIN_ROLE not in roles:
        raise HTTPException(
            Config.HttpStatus.FORBIDDEN,
            "force 승인(수정본 덮어쓰기)은 전체 관리자만 할 수 있습니다.",
        )
    return await _review(place, True, sub, force)


@router.post(
    "/{place}/review/reject",
    response_model=AdminTimetableDetail,
    responses=_REVIEW_RESPONSES,
)
async def reject(place: str, sub: ShuttleAdmin) -> dict:
    """검토 대기 파싱본을 버립니다. 같은 PDF는 다시 파싱하지 않습니다."""
    return await _review(place, False, sub)
