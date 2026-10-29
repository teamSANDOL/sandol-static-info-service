"""셔틀 시간표 관리자 API의 응답 모양 변환."""

from collections.abc import Callable
from datetime import date
from functools import partial
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Config
from app.config.shuttle_routes import DIRECTION_IDS
from app.schemas.shuttle import AdminTripIn
from app.services.shuttle_schedule import doc_range, needs_review, norm_hhmm, today
from app.services.shuttle_store import (
    RevisionConflictError,
    delete_timetable,
    doc_revision,
    edit_timetable,
    review_timetable,
    stored_trip,
    with_trip_ids,
)

_CONFLICT_MESSAGE = (
    "다른 사람이 먼저 고쳤습니다. 상세를 다시 읽고 revision을 확인하세요."
)


def admin_trip(t: dict) -> dict:
    """저장 trip → 관리자 응답(AdminTripOut 모양). 원시값 그대로 + 규칙 위반 표시.

    Args:
        t: 저장된 운행편.

    Returns:
        id·방향 id·시각 등을 담은 dict와 입력 규칙 위반 목록(`problems`).
    """
    end = t.get("departure_time_end")
    raw_dir = t.get("direction")
    out = {
        "id": t["id"],
        "direction": DIRECTION_IDS.get(raw_dir, raw_dir)
        if isinstance(raw_dir, str)
        else raw_dir,
        "day_type": t.get("day_type"),
        "kind": t.get("kind") or ("frequent" if end else "fixed"),
        "time": norm_hhmm(t.get("departure_time")),
        "end": norm_hhmm(end),
        **{
            k: t.get(k)
            for k in (
                "note",
                "day_note",
                "boarding_place",
                "valid_from",
                "valid_to",
                "period_note",
            )
        },
    }
    try:
        AdminTripIn(**{k: v for k, v in out.items() if k != "id"})
        problems: list[str] = []
    except ValidationError as exc:
        problems = [str(e["msg"]) for e in exc.errors()]
    return {**out, "problems": problems}


def admin_summary(doc: dict) -> dict:
    """문서 → 관리자 목록 1건(연도 포함 유효기간, 검토 대기 요약).

    Args:
        doc: 저장 문서.

    Returns:
        AdminTimetableSummary 모양의 dict.
    """
    rng = doc_range(doc, today())
    pending = doc.get("pending")
    return {
        "place": doc["place"],
        "title": doc.get("title"),
        "source": doc.get("source", "pdf"),
        "valid_from": rng[0] if rng else None,
        "valid_to": rng[1] if rng else None,
        "trip_count": len(doc["trips"]),
        "needs_review": needs_review(doc),
        "problems": doc.get("problems", []),
        "pending": {
            "parsed_at": pending.get("parsed_at"),
            "pdf_url": pending.get("pdf_url"),
            "trip_count": len(pending["trips"]),
            "problems": pending.get("problems", []),
        }
        if pending
        else None,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "always_active": doc.get("source") == "user" and rng is None,
        "auto_update": doc.get("source") != "user",
        "revision": doc_revision(doc),
    }


def admin_detail(doc: dict) -> dict:
    """문서 → 관리자 상세(운행편 id 포함).

    Args:
        doc: 저장 문서.

    Returns:
        AdminTimetableDetail 모양의 dict.
    """
    return {
        **admin_summary(doc),
        "pdf_url": doc.get("pdf_url"),
        "parsed_at": doc.get("parsed_at"),
        "notes": doc.get("notes", []),
        "trips": [admin_trip(t) for t in with_trip_ids(doc["trips"])],
    }


def missing(what: str = "문서") -> HTTPException:
    """대상이 없을 때의 404 예외를 만듭니다.

    Args:
        what: 없는 대상의 이름(예: "문서", "운행편").

    Returns:
        올릴 HTTPException.
    """
    return HTTPException(Config.HttpStatus.NOT_FOUND, f"{what}가 없습니다.")


def ensure_force_allowed(roles: list[str], force: bool, message: str) -> None:
    """수정본을 덮어쓰는 force는 전체 관리자(KC_ADMIN_ROLE)에게만 허용합니다.

    Args:
        roles: 토큰의 realm 롤.
        force: 요청의 force 값.
        message: 거부할 때 보여 줄 설명.

    Raises:
        HTTPException: force인데 전체 관리자 롤이 없으면 403.
    """
    if force and Config.KC_ADMIN_ROLE not in roles:
        raise HTTPException(Config.HttpStatus.FORBIDDEN, message)


def parse_if_match(if_match: str | None) -> str | None:
    """If-Match 헤더 값 → 비교할 revision.

    Args:
        if_match: 헤더 값.

    Returns:
        revision 문자열. 없거나 '*'(존재만 확인)면 None.
    """
    tag = (if_match or "").strip().removeprefix("W/").strip('"')
    return None if tag in ("", "*") else tag


def _replace_trip(doc: dict, trip_id: str, new: dict | None) -> None:
    """문서에서 id가 trip_id인 운행편을 new로 바꾸거나(None이면) 지웁니다.

    Args:
        doc: 제자리에서 바꿀 문서.
        trip_id: 대상 운행편 id.
        new: 새 운행편. None이면 지운다.

    Raises:
        KeyError: 대상 운행편이 없을 때.
    """
    idx = next((i for i, t in enumerate(doc["trips"]) if t["id"] == trip_id), None)
    if idx is None:
        raise KeyError(trip_id)
    if new is None:
        del doc["trips"][idx]
    else:
        doc["trips"][idx] = new


def _apply_patch(doc: dict, fields: dict[str, Any]) -> None:
    """제목·유효기간·안내문 변경을 문서에 제자리 반영합니다.

    Args:
        doc: 제자리에서 바꿀 문서.
        fields: 요청에서 보낸 필드만 담은 dict. 유효기간은 이미 검증된 값이어야 한다.
    """
    for key in ("title", "notes"):
        if key in fields:
            doc[key] = fields[key] if key == "title" else fields[key] or []
    if "valid_from" in fields:
        vf: date | None = fields["valid_from"]
        vt: date | None = fields["valid_to"]
        doc["valid_from"] = vf.isoformat() if vf else None
        doc["valid_to"] = vt.isoformat() if vt else None
        if vf:
            doc["ref_date"] = vf.isoformat()


async def edit_document(
    place: str, change: Callable[[dict], None], sub: str, if_match: str | None
) -> dict:
    """문서를 고칩니다. If-Match(수정 버전) 충돌은 412로 바꿉니다.

    Args:
        place: 문서 이름.
        change: 문서를 제자리에서 바꾸는 함수.
        sub: 수정한 관리자 sub.
        if_match: If-Match 헤더 값.

    Returns:
        고친 문서.

    Raises:
        HTTPException: 문서가 없으면 404, 수정 버전이 다르면 412.
    """
    try:
        doc = await edit_timetable(
            place, change, sub, if_match=parse_if_match(if_match)
        )
    except RevisionConflictError as exc:
        raise HTTPException(
            Config.HttpStatus.PRECONDITION_FAILED, _CONFLICT_MESSAGE
        ) from exc
    if doc is None:
        raise missing()
    return doc


async def remove_document(place: str, if_match: str | None) -> None:
    """문서를 지웁니다.

    Args:
        place: 문서 이름.
        if_match: If-Match 헤더 값.

    Raises:
        HTTPException: 문서가 없으면 404, 수정 버전이 다르면 412.
    """
    try:
        deleted = await delete_timetable(place, if_match=parse_if_match(if_match))
    except RevisionConflictError as exc:
        raise HTTPException(
            Config.HttpStatus.PRECONDITION_FAILED, _CONFLICT_MESSAGE
        ) from exc
    if not deleted:
        raise missing()


async def patch_document(
    place: str, fields: dict[str, Any], sub: str, if_match: str | None
) -> dict:
    """제목·유효기간·안내문을 고칩니다. 보낸 필드만 바뀝니다.

    Args:
        place: 문서 이름.
        fields: 요청에서 보낸 필드만 담은 dict.
        sub: 수정한 관리자 sub.
        if_match: If-Match 헤더 값.

    Returns:
        고친 문서.

    Raises:
        HTTPException: 유효기간이 맞지 않으면 422, 문서가 없으면 404, 충돌이면 412.
    """
    if ("valid_from" in fields) != ("valid_to" in fields):
        raise HTTPException(
            Config.HttpStatus.UNPROCESSABLE_ENTITY,
            "valid_from과 valid_to는 함께 보냅니다(둘 다 null이면 상시).",
        )
    vf: date | None = fields.get("valid_from")
    vt: date | None = fields.get("valid_to")
    if (vf is None) != (vt is None) or (vf and vt and vf > vt):
        raise HTTPException(
            Config.HttpStatus.UNPROCESSABLE_ENTITY, "유효기간이 올바르지 않습니다."
        )

    return await edit_document(
        place, partial(_apply_patch, fields=fields), sub, if_match
    )


async def add_document_trip(
    place: str, data: dict, sub: str, if_match: str | None
) -> dict:
    """운행편을 추가합니다. PDF 문서면 사용자 수정본이 됩니다.

    Args:
        place: 문서 이름.
        data: 관리자 입력(AdminTripIn 모양).
        sub: 수정한 관리자 sub.
        if_match: If-Match 헤더 값.

    Returns:
        저장된 운행편.

    Raises:
        HTTPException: 문서가 없으면 404, 충돌이면 412.
    """
    trip = stored_trip(data)
    await edit_document(place, lambda d: d["trips"].append(trip), sub, if_match)
    return trip


async def replace_document_trip(
    place: str, trip_id: str, new: dict | None, sub: str, if_match: str | None
) -> None:
    """운행편 하나를 바꾸거나(new가 None이면) 지웁니다.

    Args:
        place: 문서 이름.
        trip_id: 대상 운행편 id.
        new: 새 운행편. None이면 지운다.
        sub: 수정한 관리자 sub.
        if_match: If-Match 헤더 값.

    Raises:
        HTTPException: 문서나 운행편이 없으면 404, 충돌이면 412.
    """
    try:
        await edit_document(
            place, partial(_replace_trip, trip_id=trip_id, new=new), sub, if_match
        )
    except KeyError as exc:
        raise missing("운행편") from exc


async def review_document(
    place: str, approve: bool, sub: str, force: bool = False
) -> dict:
    """검토 대기 파싱본을 승인하거나 거부합니다.

    Args:
        place: 문서 이름.
        approve: True면 승인, False면 거부.
        sub: 처리한 관리자 sub.
        force: 승인 시 수정본을 덮어쓸지 여부.

    Returns:
        처리된 문서.

    Raises:
        HTTPException: 검토 대기가 없거나 처리할 수 없으면 409, 문서가 없으면 404.
    """
    try:
        doc = await review_timetable(place, approve, sub, force)
    except ValueError as exc:
        raise HTTPException(Config.HttpStatus.CONFLICT, str(exc)) from exc
    if doc is None:
        raise missing()
    return doc
