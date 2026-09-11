"""셔틀버스 시간표 원본 PDF 다운로드 → 좌표 파싱 → JSON 저장/조회."""

import asyncio
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from app.config.config import Config, logger
from app.config.shuttle_routes import SHUTTLE_DIRECTIONS
from app.schemas.shuttle import ParsedTrip, ShuttleTrip
from app.utils.ibookdownloader import BookDownloader
from app.utils.shuttle_pdf import parse_shuttle_pdf

_lock = asyncio.Lock()  # ponytail: 파일 단위 락, DB로 옮기기 전까지 충분


def load_store() -> dict[str, dict]:
    """place → 파싱 문서. 파일 없으면 빈 dict."""
    try:
        with open(Config.SHUTTLE_TIMETABLE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_store(store: dict[str, dict]) -> None:
    os.makedirs(Config.DATA_DIR, exist_ok=True)
    tmp = Config.SHUTTLE_TIMETABLE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, Config.SHUTTLE_TIMETABLE_PATH)


async def fetch_shuttle_pdf() -> tuple[str, str, bytes]:
    """iBook에서 셔틀 시간표 원본 PDF를 받습니다.

    Returns:
        (place, pdf_url, 내용). place는 파일명에서 확장자를 뗀 값입니다.
    """
    downloader = BookDownloader(Config.SHUTTLE_URL)
    file_list = await downloader.fetch_file_list()
    pdf_url = downloader.get_file_url(file_list)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(pdf_url, timeout=60)
        resp.raise_for_status()
    place = os.path.splitext(downloader.file_name or "셔틀버스 시간표")[0].strip()
    return place, pdf_url, resp.content


async def ingest_shuttle_pdf(force: bool = False) -> dict | None:
    """PDF 다운로드 → 파싱 → place 키로 저장. 변경이 없으면 건너뛰고 None을 반환합니다.

    - `force`가 아니고 기존 문서가 사용자 수정본(`source == "user"`)이면 덮어쓰지 않습니다.
    - `force`가 아니고 PDF sha256이 기존 문서와 같으면 파싱을 건너뜁니다.
    """
    place, pdf_url, content = await fetch_shuttle_pdf()

    async with _lock:
        existing = load_store().get(place)
    if not force and existing is not None and existing.get("source") == "user":
        logger.info("셔틀 시간표 사용자 수정본이라 파싱 건너뜀: place=%s", place)
        return None

    pdf_sha256 = hashlib.sha256(content).hexdigest()
    if not force and existing is not None and existing.get("pdf_sha256") == pdf_sha256:
        logger.info("셔틀 시간표 PDF 변경 없음, 파싱 건너뜀: place=%s", place)
        return None

    os.makedirs(Config.SHUTTLE_PDF_DIR, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", place)
    with open(os.path.join(Config.SHUTTLE_PDF_DIR, f"{slug}.pdf"), "wb") as f:
        f.write(content)

    title, parsed = parse_shuttle_pdf(content)
    trips = [
        ShuttleTrip(**t, **SHUTTLE_DIRECTIONS[t["direction"]]).model_dump()
        for t in parsed
    ]
    doc = {
        "place": place,
        "title": title,
        "pdf_url": pdf_url,
        "pdf_sha256": pdf_sha256,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "source": "pdf",
        "trips": trips,
    }
    async with _lock:
        store = load_store()
        store[place] = doc
        _save_store(store)
    logger.info("셔틀 시간표 저장: place=%s trips=%d", place, len(trips))
    return doc


async def sync_shuttle_timetables(force: bool = False) -> list[str]:
    """원본 PDF를 동기화합니다. 갱신된 place 목록을 반환합니다."""
    try:
        doc = await ingest_shuttle_pdf(force=force)
    except Exception:
        logger.exception("셔틀 시간표 동기화 실패")
        return []
    return [doc["place"]] if doc is not None else []


async def save_user_timetable(place: str, trips: list[ParsedTrip]) -> dict:
    """사용자가 직접 입력한 시간표를 저장합니다. 기존 출처 필드는 있으면 보존합니다."""
    shuttle_trips = [
        ShuttleTrip(**t.model_dump(), **SHUTTLE_DIRECTIONS[t.direction]) for t in trips
    ]
    async with _lock:
        store = load_store()
        existing = store.get(place)
        doc = {
            "place": place,
            # 출처 정보(title/pdf_*)는 기존 문서가 있으면 그대로 물려받는다
            "title": existing.get("title") if existing else None,
            "pdf_url": existing.get("pdf_url") if existing else None,
            "pdf_sha256": existing.get("pdf_sha256") if existing else None,
            "parsed_at": existing.get("parsed_at") if existing else None,
            "source": "user",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "trips": [t.model_dump() for t in shuttle_trips],
        }
        store[place] = doc
        _save_store(store)
    logger.info("셔틀 시간표 사용자 저장: place=%s trips=%d", place, len(doc["trips"]))
    return doc


async def delete_timetable(place: str) -> bool:
    """저장된 place 문서를 삭제합니다. 있었으면 True, 없었으면 False."""
    async with _lock:
        store = load_store()
        if place not in store:
            return False
        del store[place]
        _save_store(store)
    return True


def _is_valid_on(valid_from: str | None, valid_to: str | None, md: str) -> bool:
    """trip의 운행 기간(MM-DD)에 조회일(md, MM-DD)이 포함되는지 판정.

    - 둘 다 없으면 상시 운행이라 항상 포함.
    - MM-DD 형식이 아닌 값이 섞이면, 데이터 유실보다 포함시키는 편이 안전하므로 True.
    - valid_from > valid_to면 12-20~02-28처럼 해를 넘기는 구간으로 보고 OR 조건으로 판정.
    - 한쪽만 있으면 그 경계만 검사.
    """

    def is_md(v: str | None) -> bool:
        return v is not None and re.fullmatch(r"\d{2}-\d{2}", v) is not None

    if valid_from is None and valid_to is None:
        return True
    if (valid_from is not None and not is_md(valid_from)) or (
        valid_to is not None and not is_md(valid_to)
    ):
        return True
    if valid_from is not None and valid_to is not None:
        if valid_from > valid_to:  # 연말 걸침
            return md >= valid_from or md <= valid_to
        return valid_from <= md <= valid_to
    if valid_from is not None:
        return md >= valid_from
    return md <= valid_to


def query_trips(
    store: dict[str, dict],
    route: str | None = None,
    day_type: str | None = None,
    origin: str | None = None,
    destination: str | None = None,
    on_date: date | None = None,
    include_all: bool = False,
) -> list[dict]:
    """저장된 문서에서 조건에 맞는 trips를 place(출처) 필드를 붙여 평탄화합니다.

    route는 부분 일치, origin/destination은 정확히 일치하되 경유지(via)도 포함합니다.
    include_all이 아니면 on_date(없으면 오늘, KST) 기준으로 유효한 trip만 남기고,
    (day_type, direction, departure_time, departure_time_end, valid_from, valid_to) 기준으로
    중복을 제거합니다.
    """
    md = (on_date or datetime.now(ZoneInfo("Asia/Seoul")).date()).strftime("%m-%d")
    out = []
    seen = set()
    for p, doc in store.items():
        for t in doc["trips"]:
            if route and route not in t["route"]:
                continue
            if day_type and t["day_type"] != day_type:
                continue
            if origin and origin not in (t["origin"], *t["via"]):
                continue
            if destination and destination not in (t["destination"], *t["via"]):
                continue
            if not include_all and not _is_valid_on(
                t.get("valid_from"), t.get("valid_to"), md
            ):
                continue
            dedupe_key = (
                t["day_type"],
                t["direction"],
                t["departure_time"],
                t["departure_time_end"],
                t.get("valid_from"),
                t.get("valid_to"),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append({"place": p, **t})
    return out
