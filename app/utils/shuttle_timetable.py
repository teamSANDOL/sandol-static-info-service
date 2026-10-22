"""셔틀버스 시간표 원본 PDF 다운로드 → 좌표 파싱 → JSON 저장/조회."""

import asyncio
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import cache
from zoneinfo import ZoneInfo

import httpx

from app.config.config import Config, logger
from app.config.shuttle_routes import (
    DIRECTION_IDS,
    ROUTE_IDS,
    SHUTTLE_DIRECTIONS,
    STOP_IDS,
)
from app.schemas.shuttle import ParsedTrip, ShuttleTrip
from app.utils.ibookdownloader import BookDownloader
from app.utils.shuttle_pdf import parse_shuttle_pdf

KST = ZoneInfo("Asia/Seoul")
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
    - `force`가 아니고 PDF sha256이 기존 문서(또는 검토 대기본)와 같으면 건너뜁니다.
    - 새 파싱본이 검증(`validate_trips`)에 실패하면 직전 정상본을 그대로 두고 새 것은
      `pending`(검토 대기)에 넣습니다. 비교할 정상본이 없으면 새 것을 쓰되 `problems`로
      표시합니다(force일 때도 같음). 관리자가 PUT으로 고치면 풀립니다.
    """
    place, pdf_url, content = await fetch_shuttle_pdf()

    async with _lock:
        existing = load_store().get(place)
    if not force and existing is not None and existing.get("source") == "user":
        logger.info("셔틀 시간표 사용자 수정본이라 파싱 건너뜀: place=%s", place)
        return None

    pdf_sha256 = hashlib.sha256(content).hexdigest()
    known = (
        (
            existing.get("pdf_sha256"),
            (existing.get("pending") or {}).get("pdf_sha256"),
        )
        if existing
        else ()
    )
    if not force and pdf_sha256 in known:
        logger.info("셔틀 시간표 PDF 변경 없음, 파싱 건너뜀: place=%s", place)
        return None

    os.makedirs(Config.SHUTTLE_PDF_DIR, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", place)
    with open(os.path.join(Config.SHUTTLE_PDF_DIR, f"{slug}.pdf"), "wb") as f:
        f.write(content)

    title, parsed, notes, days = parse_shuttle_pdf(content)
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
        "ref_date": _today().isoformat(),
        "source": "pdf",
        "notes": notes,
        "trips": trips,
    }
    # force는 관리자가 PDF를 명시적으로 받아들이는 경로라 직전본으로 되돌리지 않습니다.
    good = None if force or not existing or existing.get("problems") else existing
    async with _lock:
        others = [d for k, d in load_store().items() if k != place]
    previous = good or _latest_good(others)
    problems = validate_trips(trips, days, previous, same_place=good is not None)
    if problems:
        logger.warning(
            "셔틀 시간표 검증 실패, 검토 대기: place=%s 문제=%s", place, problems
        )
        if good is not None:
            # 직전 정상본을 계속 내보내고 새 파싱본은 검토 대기로만 둡니다.
            doc = {**good, "pending": {**doc, "problems": problems}}
        else:
            doc["problems"] = problems  # 비교할 정상본이 없으면 그대로 쓰되 표시
    async with _lock:
        store = load_store()
        store[place] = doc
        _save_store(store)
    logger.info("셔틀 시간표 저장: place=%s trips=%d", place, len(trips))
    return doc


def needs_review(doc: dict) -> bool:
    """최신 파싱이 검증에 실패해 검토 대기 중인지(응답 source.needs_review)."""
    return bool(doc.get("pending") or doc.get("problems"))


def _latest_good(docs: list[dict]) -> dict | None:
    """검토 표시가 없는 문서 중 가장 최근 것(학기 교체 시 비교 대상)."""
    good = [d for d in docs if not d.get("problems")]
    return max(good, key=_recency, default=None)


def validate_trips(
    trips: list[dict],
    days: list[str],
    previous: dict | None,
    same_place: bool = True,
) -> list[str]:
    """파싱 직후 검사. 문제 설명 목록을 돌려주며 비어 있으면 통과입니다.

    - 노선마다 서로 반대인 방향 한 쌍 이상이 있어야 합니다.
    - 수시운행·도착버스 구간은 시작 < 종료여야 합니다.
    - 표 머리에 '토요일'이 있으면 토요일 편이 하나 이상 있어야 합니다.
    - 직전 정상본(previous)에 있던 노선이 통째로 사라지면 의심합니다.
    - 같은 place의 직전 정상본이면 편수가 절반 미만으로 줄어도 의심합니다. 학기가
      바뀐 새 place는 방학 시간표처럼 편수가 원래 크게 줄 수 있어 이 검사를 뺍니다.
    """
    problems = []
    if not trips:
        return ["운행편이 하나도 없습니다"]
    for route in sorted({t["route"] for t in trips}):
        dirs = {t["direction"] for t in trips if t["route"] == route}
        if not any(" → ".join(reversed(d.split(" → "))) in dirs for d in dirs):
            only = ", ".join(sorted(dirs))
            problems.append(f"{route}: 한쪽 방향 운행편만 있습니다({only})")
    bad = [
        t["departure_time"]
        for t in trips
        if t["kind"] != "fixed" and not t["departure_time"] < t["departure_time_end"]
    ]
    if bad:
        problems.append(f"구간 시작이 종료보다 늦습니다: {', '.join(bad)}")
    if "토요일" in days and not any(t["day_type"] == "토요일" for t in trips):
        problems.append("표 머리에 '토요일'이 있는데 토요일 운행편이 없습니다")
    if previous is None:
        return problems
    lost = {t["route"] for t in previous["trips"]} - {t["route"] for t in trips}
    if lost:
        problems.append(f"노선 소실: {', '.join(sorted(lost))}")
    before = len(previous["trips"])
    if same_place and len(trips) * 2 < before:
        problems.append(f"편수 급감: {before} → {len(trips)}")
    return problems


async def sync_shuttle_timetables(force: bool = False) -> list[str]:
    """원본 PDF를 동기화합니다. 갱신된 place 목록을 반환합니다."""
    try:
        doc = await ingest_shuttle_pdf(force=force)
    except Exception:
        logger.exception("셔틀 시간표 동기화 실패")
        return []
    return [doc["place"]] if doc is not None else []


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
    """
    shuttle_trips = [
        ShuttleTrip(**t.model_dump(), **SHUTTLE_DIRECTIONS[t.direction]) for t in trips
    ]
    async with _lock:
        store = load_store()
        existing = store.get(place)
        doc = {
            "place": place,
            "title": title,
            # PDF 출처 정보는 기존 문서가 있으면 그대로 물려받는다
            "pdf_url": existing.get("pdf_url") if existing else None,
            "pdf_sha256": existing.get("pdf_sha256") if existing else None,
            "parsed_at": existing.get("parsed_at") if existing else None,
            # 수정해도 문서의 연도 기준은 그대로(2025 문서를 2026에 고쳐도 2025)
            "ref_date": (_ref_date(existing) if existing else None)
            or _today().isoformat(),
            "source": "user",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": updated_by,
            "notes": (existing or {}).get("notes", []) if notes is None else notes,
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
    """한쪽 경계만 있거나 형식이 깨진 옛 기간(MM-DD)용 판정. 애매하면 포함합니다."""

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


# 기준일보다 이만큼 전에 이미 끝난 기간이면 다음 해의 같은 기간으로 봅니다.
_STALE = timedelta(days=60)


def _today() -> date:
    return datetime.now(KST).date()


def _md_date(year: int, md: str) -> date:
    month, day = int(md[:2]), int(md[3:])
    try:
        return date(year, month, day)
    except ValueError:  # 평년의 02-29
        return date(year, month, 28)


def _ref_date(doc: dict) -> str | None:
    """문서 연도의 기준일(YYYY-MM-DD). 원문엔 연도가 없어 이것으로 MM-DD에 연도를 붙입니다.

    순서: 저장된 ref_date → 문서 유효기간 시작 → PDF 파싱일 → 제목·place의 'NN년'
    (그해 7월 1일) → 수정일. 모두 없으면 None(조회일 기준, 옛 동작).
    """
    for key in ("ref_date", "valid_from", "parsed_at"):
        if doc.get(key):
            return str(doc[key])[:10]
    m = re.search(r"(\d{2})년", f"{doc.get('place') or ''} {doc.get('title') or ''}")
    if m:
        return f"20{m.group(1)}-07-01"
    return str(doc["updated_at"])[:10] if doc.get("updated_at") else None


def _resolve(valid_from: str, valid_to: str, ref: date) -> tuple[date, date]:
    """MM-DD 기간 → 기준일 근처의 실제 기간. 기준일 무렵 이미 끝난 해는 건너뜁니다."""
    for year in (ref.year - 1, ref.year, ref.year + 1):
        start = _md_date(year, valid_from)
        end = _md_date(year + (valid_from > valid_to), valid_to)
        if end >= ref - _STALE:
            break
    return start, end


def _is_md_pair(valid_from: str | None, valid_to: str | None) -> bool:
    return all(v and re.fullmatch(r"\d{2}-\d{2}", v) for v in (valid_from, valid_to))


def _base(doc: dict, on_date: date) -> date:
    ref = _ref_date(doc)
    return date.fromisoformat(ref) if ref else on_date


def _periods(doc: dict, on_date: date) -> list[tuple[date, date]]:
    """문서의 운행 기간들(연도 포함). 관리자가 정한 문서 기간이 있으면 그것만 씁니다."""
    if doc.get("valid_from") and doc.get("valid_to"):
        return [
            (date.fromisoformat(doc["valid_from"]), date.fromisoformat(doc["valid_to"]))
        ]
    base = _base(doc, on_date)
    pairs = {
        (t.get("valid_from"), t.get("valid_to"))
        for t in doc["trips"]
        if _is_md_pair(t.get("valid_from"), t.get("valid_to"))
    }
    return sorted(_resolve(a, b, base) for a, b in pairs)


def doc_range(doc: dict, on_date: date) -> tuple[date, date] | None:
    """문서 전체 유효기간. 기간 표기가 전혀 없으면 None(상시)."""
    periods = _periods(doc, on_date)
    if not periods:
        return None
    return min(p[0] for p in periods), max(p[1] for p in periods)


def _recency(doc: dict) -> str:
    return doc.get("updated_at") or doc.get("parsed_at") or ""


def pick_document(store: dict[str, dict], on_date: date) -> dict:
    """응답에 쓸 문서 1건.

    1. 조회일을 (연도까지) 포함하는 문서끼리만 겨룹니다. 기간 표기가 있는 문서가 상시
       문서보다, 그다음 사용자 수정본이, 그다음 최신이 앞섭니다. 그래서 지난해
       수정본이 올해 PDF를 가리지 않습니다.
    2. 포함하는 문서가 없으면 가장 최근에 끝난 문서, 그것도 없으면 가장 먼저 시작할
       문서로 빈 시간표(기간은 그 문서의 실제 기간)를 내보냅니다.
    """
    docs = list(store.values())
    ranges = [doc_range(d, on_date) for d in docs]
    covering = [
        (r is not None, d.get("source") == "user", _recency(d), i)
        for i, (d, r) in enumerate(zip(docs, ranges, strict=True))
        if r is None or r[0] <= on_date <= r[1]
    ]
    if covering:
        return docs[max(covering)[3]]
    ended = [
        (r[1], _recency(d), i)
        for i, (d, r) in enumerate(zip(docs, ranges, strict=True))
        if r and r[1] < on_date
    ]
    if ended:
        return docs[max(ended)[2]]
    return docs[min((r[0], i) for i, r in enumerate(ranges) if r)[1]]


def _period_on(doc: dict, on_date: date) -> tuple[date, date] | None:
    """응답의 valid_from/valid_to. 조회일이 든 기간, 없으면 문서 전체 기간."""
    periods = _periods(doc, on_date)
    hit = [p for p in periods if p[0] <= on_date <= p[1]]
    return hit[0] if hit else doc_range(doc, on_date)


def _trip_on(t: dict, doc: dict, on_date: date) -> bool:
    """trip이 조회일에 운행하는지(연도 포함).

    문서 기간 안이어야 하고, trip 자체 기간(MM-DD)이 있으면 그 기간에도 들어야 합니다.
    """
    rng = doc_range(doc, on_date)
    if rng is not None and not rng[0] <= on_date <= rng[1]:
        return False
    vf, vt = t.get("valid_from"), t.get("valid_to")
    if not _is_md_pair(vf, vt) or doc.get("valid_from"):
        return _is_valid_on(vf, vt, on_date.strftime("%m-%d"))
    start, end = _resolve(vf, vt, _base(doc, on_date))
    return start <= on_date <= end


DAY_TYPES = {  # 응답 day_type → 그날 쓰는 원문 운행일. '주말' 표는 토·일 양쪽.
    "weekday": {"평일"},
    "saturday": {"토요일", "주말"},
    "sunday": {"일요일", "주말"},
    "holiday": {"공휴일"},
}


@cache
def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def day_type_of(on_date: date) -> tuple[str, str | None]:
    """조회일 → (day_type, 공휴일 이름). 공휴일 파일에 없는 해는 요일로만 판정합니다."""
    holidays = _load_json(Config.HOLIDAYS_PATH)
    if on_date.year not in holidays["years"]:
        logger.warning("공휴일 파일에 %d년이 없어 요일로만 판정합니다", on_date.year)
    name = holidays["dates"].get(on_date.isoformat())
    if name:
        return "holiday", name
    return {5: "saturday", 6: "sunday"}.get(on_date.weekday(), "weekday"), None


def _segment(t: dict) -> dict:
    """저장 trip → 응답 segment. 비고·운행일 비고·탑승 장소는 note 한 줄로 합칩니다."""
    parts = (t.get("note"), t.get("day_note"), t.get("boarding_place"))
    note = " · ".join(x for x in parts if x)
    if t["kind"] == "fixed":
        return {"type": "fixed", "time": t["departure_time"], "note": note or None}
    return {
        "type": t["kind"],
        "start": t["departure_time"],
        "end": t["departure_time_end"],
        "note": note or None,
    }


def timetable_version(doc: dict, stops: list[dict], needs_review: bool) -> str:
    """날짜와 무관한 응답 재료의 해시 앞 16자(ETag로도 씁니다).

    응답 본문(date·day_type·holiday_name 제외)을 만드는 재료 전부입니다: 검토 대기본을
    뺀 저장 문서 전체(trips·notes·출처·기간·연도 기준), 정류장, needs_review, 공휴일
    파일. 같은 시간표면 날짜가 달라도 같고, 본문에 나오는 값이 바뀌면 바뀝니다.
    """
    body = {
        "doc": {k: v for k, v in doc.items() if k != "pending"},
        "stops": stops,
        "needs_review": needs_review,
        "holidays": _load_json(Config.HOLIDAYS_PATH),
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_timetable(doc: dict, on_date: date, needs_review: bool = False) -> dict:
    """저장 문서 1건 → 모바일 응답(계약: 날짜·운행일·방향별 segments).

    해당 날짜·운행일에 운행이 없는 방향도 6개 모두 `segments: []`로 넣습니다.
    """
    day_type, holiday_name = day_type_of(on_date)
    stops = _load_json(Config.SHUTTLE_STOPS_PATH)["stops"]
    period = _period_on(doc, on_date)

    by_dir: dict[str, list[dict]] = {d: [] for d in DIRECTION_IDS}
    seen = set()
    for raw in doc["trips"]:
        t = ShuttleTrip(**raw).model_dump()  # kind가 없던 옛 저장본도 기본값으로 읽음
        key = (t["direction"], t["kind"], t["departure_time"], t["departure_time_end"])
        if (
            t["day_type"] not in DAY_TYPES[day_type]
            or not _trip_on(t, doc, on_date)
            or key in seen
        ):
            continue
        seen.add(key)
        by_dir[t["direction"]].append(_segment(t))

    routes: dict[str, dict] = {}
    for direction, dir_id in DIRECTION_IDS.items():
        info = SHUTTLE_DIRECTIONS[direction]
        route = routes.setdefault(
            ROUTE_IDS[info["route"]],
            {"id": ROUTE_IDS[info["route"]], "name": info["route"], "directions": []},
        )
        segments = sorted(by_dir[direction], key=lambda g: g.get("time") or g["start"])
        route["directions"].append(
            {
                "id": dir_id,
                "from": STOP_IDS[info["origin"]],
                "to": STOP_IDS[info["destination"]],
                "via": [STOP_IDS[v] for v in info["via"]],
                "segments": segments,
            }
        )
    return {
        "date": on_date.isoformat(),
        "day_type": day_type,
        "holiday_name": holiday_name,
        "version": timetable_version(doc, stops, needs_review),
        "valid_from": period[0].isoformat() if period else None,
        "valid_to": period[1].isoformat() if period else None,
        "source": {
            "title": doc.get("place"),
            "pdf_url": doc.get("pdf_url"),
            "parsed_at": doc.get("parsed_at"),
            "origin": doc.get("source"),
            "needs_review": needs_review,
        },
        "stops": stops,
        "routes": list(routes.values()),
        "notes": doc.get("notes", []),
    }
