"""셔틀 시간표 JSON 저장소: 읽기·쓰기, 관리자 편집·검토, 수정 버전(revision)."""

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.config import Config, logger
from app.config.shuttle_routes import DIRECTION_IDS, SHUTTLE_DIRECTIONS
from app.schemas.shuttle import ParsedTrip, ShuttleTrip
from app.services.shuttle_schedule import ref_date, today


store_lock = asyncio.Lock()  # ponytail: 파일 단위 락, DB로 옮기기 전까지 충분


def load_store() -> dict[str, dict]:
    """저장된 시간표 문서를 읽습니다.

    Returns:
        place → 문서. 파일이 없으면 빈 dict.
    """
    try:
        with open(Config.SHUTTLE_TIMETABLE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_store(store: dict[str, dict]) -> None:
    """저장소를 임시 파일에 쓴 뒤 교체해 원자적으로 저장합니다. 호출자가 store_lock을 잡아야 합니다.

    Args:
        store: place → 문서.
    """
    os.makedirs(Config.DATA_DIR, exist_ok=True)
    tmp = Config.SHUTTLE_TIMETABLE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, Config.SHUTTLE_TIMETABLE_PATH)


def doc_revision(doc: dict) -> str:
    """저장 문서의 수정 버전(If-Match 비교용). 문서가 바뀌면 바뀝니다.

    Args:
        doc: 저장 문서.

    Returns:
        16자 16진 해시.
    """
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RevisionConflictError(Exception):
    """If-Match로 받은 수정 버전이 저장 문서와 다릅니다(다른 사람이 먼저 고침)."""


_DIRECTION_BY_ID = {v: k for k, v in DIRECTION_IDS.items()}


_TRIP_CORE = (
    "direction",
    "day_type",
    "kind",
    "departure_time",
    "departure_time_end",
    "valid_from",
    "valid_to",
)


def with_trip_ids(trips: list[dict]) -> list[dict]:
    """id 없는 trip에 내용 기반 id를 붙입니다(같은 내용이면 순번으로 구분).

    내용 기반이라 문서를 다시 읽어도 같은 id가 나오고, 한 번 저장되면 그 trip을
    고쳐도 id는 그대로입니다.

    Args:
        trips: 저장 trip 목록.

    Returns:
        모든 trip에 id가 있는 새 목록.
    """
    seen: dict[str, int] = {}
    out = []
    for t in trips:
        if t.get("id"):
            out.append(t)
            continue
        core = json.dumps([t.get(k) for k in _TRIP_CORE], ensure_ascii=False)
        n = seen[core] = seen.get(core, -1) + 1
        out.append({**t, "id": hashlib.sha256(f"{core}#{n}".encode()).hexdigest()[:12]})
    return out


def stored_trip(data: dict, trip_id: str | None = None) -> dict:
    """관리자 입력(AdminTripIn 모양) → 저장 trip.

    Args:
        data: 관리자 입력.
        trip_id: 유지할 운행편 id. None이면 새로 만든다.

    Returns:
        id를 포함한 저장 trip.
    """
    direction = _DIRECTION_BY_ID[data["direction"]]
    parsed = ParsedTrip(
        direction=direction,
        day_type=data["day_type"],
        day_note=data.get("day_note"),
        departure_time=data["time"],
        departure_time_end=data.get("end"),
        valid_from=data.get("valid_from"),
        valid_to=data.get("valid_to"),
        period_note=data.get("period_note"),
        boarding_place=data.get("boarding_place"),
        note=data.get("note"),
        kind=data.get("kind") or "fixed",
    )
    trip = ShuttleTrip(**parsed.model_dump(), **SHUTTLE_DIRECTIONS[direction])
    return {**trip.model_dump(), "id": trip_id or os.urandom(6).hex()}


async def save_user_timetable(
    place: str,
    trips: list[ParsedTrip],
    title: str | None,
    updated_by: str | None = None,
    notes: list[str] | None = None,
) -> dict:
    """관리자가 직접 입력한 시간표를 저장합니다.

    같은 place 안에서는 수동 입력이 PDF보다 우선합니다(source="user"는 force 없는
    동기화가 덮어쓰지 않음). title은 입력값으로 바꾸고, PDF 출처 필드는 있으면 보존합니다.
    notes가 None이면 기존 문서의 안내문을 물려받습니다.

    Args:
        place: 시간표 이름(문서 키).
        trips: 저장할 출발 목록.
        title: 문서 제목.
        updated_by: 저장한 관리자 sub.
        notes: 표 밖 안내문. None이면 기존 값을 유지한다.

    Returns:
        저장된 문서.
    """
    shuttle_trips = [
        ShuttleTrip(**t.model_dump(), **SHUTTLE_DIRECTIONS[t.direction]) for t in trips
    ]
    async with store_lock:
        store = load_store()
        existing = store.get(place)
        doc: dict[str, Any] = {
            "place": place,
            "title": title,
            # PDF 출처 정보는 기존 문서가 있으면 그대로 물려받는다
            "pdf_url": existing.get("pdf_url") if existing else None,
            "pdf_sha256": existing.get("pdf_sha256") if existing else None,
            "parsed_at": existing.get("parsed_at") if existing else None,
            # 수정해도 문서의 연도 기준은 그대로(2025 문서를 2026에 고쳐도 2025)
            "ref_date": (ref_date(existing) if existing else None)
            or today().isoformat(),
            # 관리자 API로 정한 연도 포함 유효기간도 이어받는다
            "valid_from": (existing or {}).get("valid_from"),
            "valid_to": (existing or {}).get("valid_to"),
            "source": "user",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
            "notes": (existing or {}).get("notes", []) if notes is None else notes,
            "trips": with_trip_ids([t.model_dump() for t in shuttle_trips]),
        }
        store[place] = doc
        save_store(store)
    logger.info("셔틀 시간표 사용자 저장: place=%s trips=%d", place, len(doc["trips"]))
    return doc


async def delete_timetable(place: str, if_match: str | None = None) -> bool:
    """저장된 place 문서를 삭제합니다.

    Args:
        place: 삭제할 문서 이름.
        if_match: 수정 버전. 주면 저장 문서와 같을 때만 지운다.

    Returns:
        있었으면 True, 없었으면 False.

    Raises:
        RevisionConflictError: if_match가 저장 문서의 수정 버전과 다를 때.
    """
    async with store_lock:
        store = load_store()
        if place not in store:
            return False
        if if_match is not None and if_match != doc_revision(store[place]):
            raise RevisionConflictError(place)
        del store[place]
        save_store(store)
    return True


async def create_timetable(data: dict, updated_by: str) -> dict | None:
    """관리자가 새 문서를 만듭니다.

    유효기간(연도 포함)을 주면 그 시작일이 문서의 연도 기준일이 됩니다.

    Args:
        data: 관리자 입력(place·title·valid_from·valid_to·notes·trips).
        updated_by: 만든 관리자 sub.

    Returns:
        만들어진 문서. 같은 place가 이미 있으면 None.
    """
    now = datetime.now(timezone.utc).isoformat()
    vf, vt = data.get("valid_from"), data.get("valid_to")
    doc = {
        "place": data["place"],
        "title": data.get("title"),
        "pdf_url": None,
        "pdf_sha256": None,
        "parsed_at": None,
        "ref_date": (vf or today()).isoformat(),
        "valid_from": vf.isoformat() if vf else None,
        "valid_to": vt.isoformat() if vt else None,
        "source": "user",
        "updated_at": now,
        "updated_by": updated_by,
        "notes": data.get("notes") or [],
        "trips": [stored_trip(t) for t in data.get("trips") or []],
    }
    async with store_lock:
        store = load_store()
        if doc["place"] in store:
            return None
        store[doc["place"]] = doc
        save_store(store)
    return doc


async def edit_timetable(
    place: str,
    change: Callable[[dict], None],
    updated_by: str,
    if_match: str | None = None,
) -> dict | None:
    """문서를 고칩니다. change가 ValueError·KeyError를 내면 저장하지 않습니다.

    고친 문서는 사용자 수정본(source=user)이 되어 force 없는 동기화가 덮어쓰지 않고,
    사람이 본 것이므로 첫 파싱 실패 표시(problems)도 지웁니다. PDF 문서의 연도
    기준일은 그대로 이어받습니다. 검토 대기본(pending)은 승인·거부로만 처리합니다.

    Args:
        place: 문서 이름.
        change: 문서를 제자리에서 바꾸는 함수.
        updated_by: 고친 관리자 sub.
        if_match: 수정 버전. 주면 저장 문서와 같을 때만 고친다.

    Returns:
        고친 문서. 문서가 없으면 None.

    Raises:
        RevisionConflictError: if_match가 저장 문서의 수정 버전과 다를 때.
    """
    async with store_lock:
        store = load_store()
        if place not in store:
            return None
        if if_match is not None and if_match != doc_revision(store[place]):
            raise RevisionConflictError(place)
        doc = {**store[place], "trips": with_trip_ids(store[place]["trips"])}
        doc["ref_date"] = ref_date(doc) or today().isoformat()
        change(doc)
        doc.pop("problems", None)
        doc.update(
            source="user",
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=updated_by,
        )
        store[place] = doc
        save_store(store)
    return doc


async def review_timetable(
    place: str, approve: bool, updated_by: str, force: bool = False
) -> dict | None:
    """검토 대기 파싱본을 승인(정상본으로 승격)하거나 거부합니다.

    - 승인: pending이 있으면 그것으로 교체, 첫 파싱 실패(problems)면 표시만 지웁니다.
      검토 대기본이 생긴 뒤 관리자가 문서를 고쳤다면(source=user) 그 수정이 조용히
      사라지지 않게 force일 때만 덮어씁니다.
    - 거부: pending을 버리고 그 PDF 해시를 기억해 같은 PDF를 다시 파싱하지 않습니다.
      problems만 있는 문서는 되돌릴 정상본이 없어 거부할 수 없습니다(수정·삭제).

    Args:
        place: 문서 이름.
        approve: True면 승인, False면 거부.
        updated_by: 처리한 관리자 sub.
        force: 승인 시 수정본을 파싱본으로 덮어쓸지 여부.

    Returns:
        처리된 문서. 문서가 없으면 None.

    Raises:
        ValueError: 검토 대기가 없거나 거부할 수 없는 상태일 때, 승인이 수정본을 덮어쓰게 될 때.
    """
    async with store_lock:
        store = load_store()
        doc = store.get(place)
        if doc is None:
            return None
        pending = doc.get("pending")
        if approve and pending and doc.get("source") == "user" and not force:
            raise ValueError(
                "검토 대기본이 생긴 뒤 문서가 수정됐습니다. 승인하면 수정 내용이 "
                "파싱본으로 덮어써지므로 의도했다면 force=true로 다시 요청하세요."
            )
        if approve and pending:
            doc = {k: v for k, v in pending.items() if k != "problems"}
        elif approve and doc.get("problems"):
            doc = {k: v for k, v in doc.items() if k != "problems"}
        elif not approve and pending:
            doc = {k: v for k, v in doc.items() if k != "pending"}
            doc["rejected_sha256"] = pending.get("pdf_sha256")
        elif doc.get("problems"):
            raise ValueError("되돌릴 정상본이 없습니다. 운행편을 고치거나 삭제하세요.")
        else:
            raise ValueError("검토 대기 중인 파싱본이 없습니다.")
        doc["reviewed_by"] = updated_by
        store[place] = doc
        save_store(store)
    return doc
