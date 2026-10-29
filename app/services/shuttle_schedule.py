"""셔틀 시간표의 날짜 판정(연도 해석·문서 선택·운행일)과 공개 응답 조립."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.config import Config, logger
from app.config.shuttle_routes import (
    DIRECTION_IDS,
    ROUTE_IDS,
    SHUTTLE_DIRECTIONS,
    STOP_IDS,
)
from app.schemas.shuttle import ShuttleTrip


KST = ZoneInfo("Asia/Seoul")


# 기준일보다 이만큼 전에 이미 끝난 기간이면 다음 해의 같은 기간으로 봅니다.
_STALE = timedelta(days=60)


def today() -> date:
    """오늘 날짜(KST).

    Returns:
        KST 기준 오늘.
    """
    return datetime.now(KST).date()


def _md_date(year: int, md: str) -> date:
    """연도와 MM-DD로 날짜를 만듭니다. 평년의 02-29는 02-28로 바꿉니다.

    Args:
        year: 연도.
        md: `MM-DD` 문자열.

    Returns:
        해당 날짜.
    """
    month, day = int(md[:2]), int(md[3:])
    try:
        return date(year, month, day)
    except ValueError:  # 평년의 02-29
        return date(year, month, 28)


def ref_date(doc: dict) -> str | None:
    """문서 연도의 기준일(YYYY-MM-DD). 원문엔 연도가 없어 이것으로 MM-DD에 연도를 붙입니다.

    순서: 저장된 ref_date → 문서 유효기간 시작 → PDF 파싱일 → 제목·place의 'NN년'
    (그해 7월 1일) → 수정일. 모두 없으면 None(조회일 기준, 옛 동작).

    Args:
        doc: 저장 문서.

    Returns:
        기준일 문자열. 근거가 없으면 None.
    """
    for key in ("ref_date", "valid_from", "parsed_at"):
        if doc.get(key):
            return str(doc[key])[:10]
    m = re.search(r"(\d{2})년", f"{doc.get('place') or ''} {doc.get('title') or ''}")
    if m:
        return f"20{m.group(1)}-07-01"
    return str(doc["updated_at"])[:10] if doc.get("updated_at") else None


def _resolve(valid_from: str, valid_to: str, ref: date) -> tuple[date, date]:
    """MM-DD 기간 → 기준일 근처의 실제 기간. 기준일 무렵 이미 끝난 해는 건너뜁니다.

    Args:
        valid_from: 시작 MM-DD.
        valid_to: 종료 MM-DD. 시작보다 앞서면 해를 넘기는 기간.
        ref: 기준일.

    Returns:
        (시작일, 종료일).
    """
    for year in (ref.year - 1, ref.year, ref.year + 1):
        start = _md_date(year, valid_from)
        end = _md_date(year + (valid_from > valid_to), valid_to)
        if end >= ref - _STALE:
            break
    return start, end


def _is_md_pair(valid_from: str | None, valid_to: str | None) -> bool:
    """두 값이 모두 `MM-DD` 형식인지 확인합니다.

    Args:
        valid_from: 시작 값.
        valid_to: 종료 값.

    Returns:
        둘 다 형식에 맞으면 True.
    """
    return all(v and re.fullmatch(r"\d{2}-\d{2}", v) for v in (valid_from, valid_to))


def _base(doc: dict, on_date: date) -> date:
    """MM-DD에 연도를 붙일 기준일을 정합니다.

    Args:
        doc: 저장 문서.
        on_date: 조회일(문서에 기준일이 없을 때 대신 쓴다).

    Returns:
        문서의 기준일 또는 조회일.
    """
    ref = ref_date(doc)
    return date.fromisoformat(ref) if ref else on_date


def _periods(doc: dict, on_date: date) -> list[tuple[date, date]]:
    """문서의 운행 기간들(연도 포함). 관리자가 정한 문서 기간이 있으면 그것만 씁니다.

    Args:
        doc: 저장 문서.
        on_date: 조회일(기준일 보정에 쓴다).

    Returns:
        (시작일, 종료일) 목록(오름차순).
    """
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
    """문서 전체 유효기간. 기간 표기가 전혀 없으면 None(상시).

    Args:
        doc: 저장 문서.
        on_date: 조회일(기준일 보정에 쓴다).

    Returns:
        (가장 이른 시작일, 가장 늦은 종료일). 상시 문서면 None.
    """
    periods = _periods(doc, on_date)
    if not periods:
        return None
    return min(p[0] for p in periods), max(p[1] for p in periods)


def recency(doc: dict) -> str:
    """문서의 최신성 비교 키.

    Args:
        doc: 저장 문서.

    Returns:
        updated_at, 없으면 parsed_at, 없으면 빈 문자열.
    """
    return doc.get("updated_at") or doc.get("parsed_at") or ""


def pick_document(store: dict[str, dict], on_date: date) -> dict:
    """응답에 쓸 문서 1건을 고릅니다.

    1. 조회일을 (연도까지) 포함하는 문서끼리만 겨룹니다. 그중 관리자 문서(source
       user)가 기간 유무와 무관하게 먼저이고(기간 없는 관리자 문서는 상시라 매일
       유효), 다음은 기간 표기가 있는 문서, 다음은 최신입니다. 지난해 수정본은 올해
       날짜를 포함하지 않으므로 올해 PDF를 가리지 않습니다.
    2. 포함하는 문서가 없으면 가장 최근에 끝난 문서, 그것도 없으면 가장 먼저 시작할
       문서로 빈 시간표(기간은 그 문서의 실제 기간)를 내보냅니다.

    Args:
        store: place → 저장 문서. 비어 있으면 안 된다.
        on_date: 조회일.

    Returns:
        선택된 문서.
    """
    docs = list(store.values())
    ranges = [doc_range(d, on_date) for d in docs]
    covering = [
        (d.get("source") == "user", r is not None, recency(d), i)
        for i, (d, r) in enumerate(zip(docs, ranges, strict=True))
        if r is None or r[0] <= on_date <= r[1]
    ]
    if covering:
        return docs[max(covering)[3]]
    ended = [
        (r[1], recency(d), i)
        for i, (d, r) in enumerate(zip(docs, ranges, strict=True))
        if r and r[1] < on_date
    ]
    if ended:
        return docs[max(ended)[2]]
    return docs[min((r[0], i) for i, r in enumerate(ranges) if r)[1]]


def _period_on(doc: dict, on_date: date) -> tuple[date, date] | None:
    """응답의 valid_from/valid_to. 조회일이 든 기간, 없으면 문서 전체 기간.

    Args:
        doc: 저장 문서.
        on_date: 조회일.

    Returns:
        (시작일, 종료일). 기간 표기가 없으면 None.
    """
    periods = _periods(doc, on_date)
    hit = [p for p in periods if p[0] <= on_date <= p[1]]
    return hit[0] if hit else doc_range(doc, on_date)


def _is_md(value: str | None) -> bool:
    """값이 MM-DD 형식인지 확인합니다."""
    return value is not None and re.fullmatch(r"\d{2}-\d{2}", value) is not None


def _is_valid_on(valid_from: str | None, valid_to: str | None, md: str) -> bool:
    """한쪽 경계만 있거나 형식이 깨진 옛 기간(MM-DD)용 판정. 애매하면 포함합니다.

    Args:
        valid_from: 운행 시작일 MM-DD 또는 None.
        valid_to: 운행 종료일 MM-DD 또는 None.
        md: 조회일 MM-DD.

    Returns:
        조회일이 기간에 포함되면 True.
    """
    if valid_from is None and valid_to is None:
        return True
    if (valid_from is not None and not _is_md(valid_from)) or (
        valid_to is not None and not _is_md(valid_to)
    ):
        return True
    if valid_from is not None and valid_to is not None:
        if valid_from > valid_to:  # 연말 걸침
            return md >= valid_from or md <= valid_to
        return valid_from <= md <= valid_to
    if valid_from is not None:
        return md >= valid_from
    return valid_to is not None and md <= valid_to


def _trip_on(t: dict, doc: dict, on_date: date) -> bool:
    """trip이 조회일에 운행하는지(연도 포함).

    문서 기간 안이어야 하고, trip 자체 기간(MM-DD)이 있으면 그 기간에도 들어야 합니다.

    Args:
        t: 저장 trip.
        doc: trip이 속한 문서.
        on_date: 조회일.

    Returns:
        운행하면 True.
    """
    rng = doc_range(doc, on_date)
    if rng is not None and not rng[0] <= on_date <= rng[1]:
        return False
    vf, vt = t.get("valid_from"), t.get("valid_to")
    if vf is None or vt is None or not _is_md_pair(vf, vt) or doc.get("valid_from"):
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
    """JSON 정적 파일을 읽어 캐시합니다.

    Args:
        path: 파일 경로.

    Returns:
        파싱된 JSON 객체.
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def day_type_of(on_date: date) -> tuple[str, str | None]:
    """조회일 → (day_type, 공휴일 이름). 공휴일 파일에 없는 해는 요일로만 판정합니다.

    Args:
        on_date: 조회일.

    Returns:
        (weekday·saturday·sunday·holiday 중 하나, 공휴일 이름 또는 None).
    """
    holidays = _load_json(Config.HOLIDAYS_PATH)
    if on_date.year not in holidays["years"]:
        logger.warning("공휴일 파일에 %d년이 없어 요일로만 판정합니다", on_date.year)
    name = holidays["dates"].get(on_date.isoformat())
    if name:
        return "holiday", name
    return {5: "saturday", 6: "sunday"}.get(on_date.weekday(), "weekday"), None


def _segment(t: dict) -> dict:
    """저장 trip → 응답 segment. 비고·운행일 비고·탑승 장소는 note 한 줄로 합칩니다.

    Args:
        t: 저장 trip.

    Returns:
        fixed면 time, 그 밖엔 start·end를 가진 segment dict.
    """
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

    Args:
        doc: 저장 문서.
        stops: 정류장 목록.
        needs_review: 검토 대기 여부.

    Returns:
        16자 16진 문자열.
    """
    body = {
        "doc": {k: v for k, v in doc.items() if k != "pending"},
        "stops": stops,
        "needs_review": needs_review,
        "holidays": _load_json(Config.HOLIDAYS_PATH),
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def norm_hhmm(v: object) -> object:
    """옛 저장값 'H:MM'을 'HH:MM'으로. 그 밖의 값은 그대로 둡니다.

    Args:
        v: 저장된 시각 값.

    Returns:
        정규화된 값.
    """
    if isinstance(v, str) and re.fullmatch(r"\d:[0-5]\d", v.strip()):
        return "0" + v.strip()
    return v


def read_trip(raw: dict, place: str | None = None) -> dict | None:
    """저장 trip을 읽기용으로 검증합니다. 해석할 수 없는 편은 경고만 남기고 None.

    쓰기(관리자 입력)는 엄격하게 막지만, 예전에 저장된 값 하나 때문에 공개 응답
    전체가 500이 되면 안 되므로 읽을 때는 'H:MM'을 고쳐 읽고 나머지는 건너뜁니다.
    kind가 없던 옛 저장본도 기본값으로 읽습니다.

    Args:
        raw: 저장된 trip.
        place: 경고 로그에 남길 문서 이름.

    Returns:
        검증된 trip dict(id 포함). 해석할 수 없으면 None.
    """
    fixed = {
        **raw,
        "departure_time": norm_hhmm(raw.get("departure_time")),
        "departure_time_end": norm_hhmm(raw.get("departure_time_end")),
    }
    try:
        return {**ShuttleTrip(**fixed).model_dump(), "id": raw.get("id")}
    except ValidationError as exc:
        logger.warning(
            "셔틀 trip 해석 불가로 건너뜀: place=%r trip=%r 오류=%d건",
            place,
            {k: raw.get(k) for k in ("direction", "day_type", "departure_time")},
            exc.error_count(),
        )
        return None


def needs_review(doc: dict) -> bool:
    """최신 파싱이 검증에 실패해 검토 대기 중인지(응답 source.needs_review).

    Args:
        doc: 저장 문서.

    Returns:
        pending 또는 problems가 있으면 True.
    """
    return bool(doc.get("pending") or doc.get("problems"))


def build_timetable(doc: dict, on_date: date, needs_review: bool = False) -> dict:
    """저장 문서 1건 → 모바일 응답(계약: 날짜·운행일·방향별 segments).

    해당 날짜·운행일에 운행이 없는 방향도 6개 모두 `segments: []`로 넣습니다.

    Args:
        doc: 응답에 쓸 저장 문서.
        on_date: 조회일.
        needs_review: 검토 대기 여부(응답 source에 그대로 담는다).

    Returns:
        응답 본문 dict.
    """
    day_type, holiday_name = day_type_of(on_date)
    stops = _load_json(Config.SHUTTLE_STOPS_PATH)["stops"]
    period = _period_on(doc, on_date)

    by_dir: dict[str, list[dict]] = {d: [] for d in DIRECTION_IDS}
    seen = set()
    for raw in doc["trips"]:
        t = read_trip(raw, doc.get("place"))
        if t is None:
            continue
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
