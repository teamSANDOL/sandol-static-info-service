"""셔틀버스 시간표 이미지 다운로드 → OpenAI 파싱 → JSON 저장/조회."""

import asyncio
import base64
import hashlib
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from openai import AsyncOpenAI

from app.config.config import Config, logger
from app.config.shuttle_routes import SHUTTLE_DIRECTIONS
from app.schemas.shuttle import ParsedTimetable, ParsedTrip, ShuttleTimetable, ShuttleTrip
from app.utils.ibookdownloader import BookDownloader

PROMPT = """이 이미지는 한국공학대학교 셔틀버스 시간표입니다.

이미지 안에 있는 모든 시간표(노선/표가 여러 개면 전부)의 모든 출발 시각을 빠짐없이 추출해 trips 배열로 만드세요.
먼저 표(노선 × 방향 × 운행일 블록)가 몇 개인지 세고, 각 표를 하나씩 끝까지 옮긴 뒤 다음 표로 넘어가세요.
시간표가 아닌 안내도/지도만 있으면 빈 배열을 반환하세요. 규칙:
- 출발 시각 1개 = trips 1건. 같은 시각이라도 방향이 다르면 별도 건. 같은 표 안에서 중복 금지.
- direction은 아래 6개 중 하나. 표 머리글을 이 값으로 옮기세요.
  '학교 출발(하교)' → '본교 → 정왕역', '정왕역 출발(등교)' → '정왕역 → 본교',
  '본교 ⇒ 2캠퍼스' → '본교 → 제2캠퍼스', '제2캠퍼스 ⇒ 본교' → '제2캠퍼스 → 본교',
  '정왕역⇒본교⇒2캠퍼스' → '정왕역 → 본교 → 제2캠퍼스', '2캠퍼스⇒본교⇒정왕역' → '제2캠퍼스 → 본교 → 정왕역'.
- 행 머리글이 시(hour), 셀 값이 분(minute)인 격자형 표는 각 셀을 HH:MM으로 조합. 빈 셀은 건너뜀. 셀 개수를 그대로 반영하고 없는 시각을 만들지 마세요.
- '~ 17분 (막차)'처럼 행의 시와 분이 따로 적힌 경우도 HH:MM으로 조합하고 note='막차'.
- 표 셀 안에 괄호로 적힌 시각(예: '(정왕역출발 8:55, 9시)' → 08:55, 09:00)도 출발 건으로 포함. '정왕역출발' 표기가 있으면 direction='정왕역 → 본교 → 제2캠퍼스', 괄호 설명은 note에.
- day_type은 평일/토요일/일요일/주말/공휴일 중 하나. 운행일 옆 괄호(예: 일학습병행학부)는 day_note에.
- '08:40~10:00 수시운행'처럼 범위면 departure_time=08:40, departure_time_end=10:00, note='수시운행'.
- 각주/안내문(탑승 장소, 막차 등)은 그 각주가 놓인 열(방향)과 시간대에 해당하는 trips에만 boarding_place 또는 note로 반영. 다른 열에는 적용하지 말 것.
- title에는 이미지 상단 제목 원문을 그대로 옮기세요. 시간표가 아니면 null.
- 운행 기간: 제목 옆이나 표 위/우측의 기간 표기(예: '★★ 8.25 ~ 8.31 ★★', '7.14 ~ 8.24')가 있으면
  그 표의 모든 trip에 valid_from/valid_to를 MM-DD로 채우세요. 연도는 쓰지 마세요.
- 통합표(한 표 안에서 행마다 계절학기/단축근무/정상근무 같은 라벨과 색으로 서로 다른 시각을 함께 표기)인
  경우, 표 위 머리말의 '라벨(기간)' 매핑(예: '계절학기(6.23~7.13), 단축근무(7/1~8/24), 정상근무(6/23~6/30, 8/25~8/31)')을
  참고해 각 행 라벨에 해당하는 기간을 그 행의 trips에 적용하고, 라벨을 period_note에 넣으세요.
  라벨이 없는 행은 그 표의 공통 기간을 적용하세요.
- 한 라벨의 기간이 두 구간 이상이면(예: 정상근무 '6/23~6/30, 8/25~8/31') 같은 출발 시각을 구간 수만큼
  별도 trip으로 나누세요(trip 1건은 구간 1개만 가짐).
- 기간 표기가 아예 없으면(학기 중 상시 시간표) valid_from/valid_to는 null로 두세요.
- 이미지에 없는 내용은 만들지 마세요."""

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


async def parse_shuttle_image(image: bytes, mime: str) -> ShuttleTimetable:
    """OpenAI structured output으로 이미지에서 시간표를 추출합니다.

    reasoning effort는 high가 기본. medium은 표 하나를 통째로 빠뜨리는 경우가 있었고,
    표 단위로 나눠 여러 번 호출하는 방식은 노선명 불일치/중복이 생겨 단일 호출로 둡니다.
    """
    client = AsyncOpenAI(
        api_key=Config.OPENAI_API_KEY, timeout=Config.OPENAI_TIMEOUT, max_retries=3
    )
    data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    resp = await client.responses.parse(
        model=Config.OPENAI_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ],
            }
        ],
        text_format=ParsedTimetable,
        reasoning={"effort": Config.OPENAI_REASONING_EFFORT},
        service_tier=Config.OPENAI_SERVICE_TIER,
    )
    if resp.output_parsed is None:
        raise RuntimeError(f"OpenAI 응답 파싱 실패: {resp.output_text[:200]}")
    return ShuttleTimetable(
        title=resp.output_parsed.title,
        trips=[
            ShuttleTrip(**t.model_dump(), **SHUTTLE_DIRECTIONS[t.direction])
            for t in resp.output_parsed.trips
        ],
    )


VOTES = 3  # 동시 요청 수
EXTRA_VOTES = 2  # 불일치 시 추가 요청 수


def trip_keys(timetable: ShuttleTimetable) -> Counter:
    """결과 비교용 키 multiset. 방향은 enum이라 그대로, 표기가 흔들리는 비고(period_note/title)는 제외.

    운행 기간(valid_from/valid_to)이 다르면 다른 운행 건이므로 키에 포함합니다.
    """
    return Counter(
        (t.day_type, t.direction, t.departure_time, t.departure_time_end, t.valid_from, t.valid_to)
        for t in timetable.trips
    )


def similarity(a: Counter, b: Counter) -> float:
    """multiset Jaccard: 0(전혀 다름) ~ 1(동일)."""
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 1.0


async def parse_shuttle_image_consensus(
    image: bytes, mime: str
) -> tuple[ShuttleTimetable, dict]:
    """동시 3회 파싱 → 전부 일치하면 채택, 아니면 2회 추가 후 나머지와 가장 잘 맞는 결과 채택.

    Returns:
        (채택된 시간표, {"runs": 실행 수, "unanimous": 3개 일치 여부, "agreement": 채택 결과의 평균 유사도})
    """
    results = list(await asyncio.gather(*(parse_shuttle_image(image, mime) for _ in range(VOTES))))
    keys = [trip_keys(r) for r in results]
    if all(k == keys[0] for k in keys):
        return results[0], {"runs": VOTES, "unanimous": True, "agreement": 1.0}

    logger.warning("셔틀 파싱 %d회 불일치, %d회 추가 실행", VOTES, EXTRA_VOTES)
    results += await asyncio.gather(*(parse_shuttle_image(image, mime) for _ in range(EXTRA_VOTES)))
    keys = [trip_keys(r) for r in results]
    scores = [
        sum(similarity(keys[i], keys[j]) for j in range(len(keys)) if j != i) / (len(keys) - 1)
        for i in range(len(keys))
    ]
    best = max(range(len(results)), key=lambda i: (scores[i], len(results[i].trips)))
    logger.info("셔틀 파싱 유사도: %s → %d번 채택", [round(x, 3) for x in scores], best)
    return results[best], {"runs": len(results), "unanimous": False, "agreement": round(scores[best], 3)}


async def ingest_shuttle_image(
    place: str, image_url: str, page: int, force: bool = False
) -> dict | None:
    """이미지 다운로드 → 파싱 → place 키로 저장. 변경이 없으면 건너뛰고 None을 반환합니다.

    - `force`가 아니고 기존 문서가 사용자 수정본(`source == "user"`)이면 AI 파싱을 건너뜁니다(다운로드도 하지 않음).
    - `force`가 아니고 이미지 sha256이 기존 문서와 같으면(변경 없음) 파싱을 건너뜁니다.
      OpenAI 호출이 비싸므로 이 가드가 핵심입니다.
    """
    async with _lock:
        existing = load_store().get(place)
    if not force and existing is not None and existing.get("source") == "user":
        logger.info("셔틀 시간표 사용자 수정본이라 AI 파싱 건너뜀: place=%s", place)
        return None

    async with httpx.AsyncClient() as client:
        resp = await client.get(image_url, timeout=30)
        resp.raise_for_status()
    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    image_sha256 = hashlib.sha256(resp.content).hexdigest()

    if not force and existing is not None and existing.get("image_sha256") == image_sha256:
        logger.info("셔틀 시간표 이미지 변경 없음, 파싱 건너뜀: place=%s", place)
        return None

    os.makedirs(Config.SHUTTLE_IMAGE_DIR, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", place)
    with open(os.path.join(Config.SHUTTLE_IMAGE_DIR, f"{slug}.jpg"), "wb") as f:
        f.write(resp.content)

    timetable, votes = await parse_shuttle_image_consensus(resp.content, mime)
    doc = {
        "place": place,
        "page": page,
        "title": timetable.title,
        "image_url": image_url,
        "image_sha256": image_sha256,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "source": "ai",
        "model": Config.OPENAI_MODEL,
        "votes": votes,
        "trips": [t.model_dump() for t in timetable.trips],
    }
    async with _lock:
        store = load_store()
        store[place] = doc
        _save_store(store)
    logger.info("셔틀 시간표 저장: place=%s trips=%d votes=%s", place, len(doc["trips"]), votes)
    return doc


async def sync_shuttle_timetables(force: bool = False) -> list[str]:
    """iBook의 셔틀 이미지 목록을 받아 place별로 ingest_shuttle_image를 호출합니다.

    place별 예외는 잡아서 계속 진행하며, 실제로 갱신된(None이 아닌) place 목록을 반환합니다.
    """
    image_urls = await BookDownloader(Config.SHUTTLE_URL).fetch_image_list()
    updated = []
    for idx, url in enumerate(image_urls, start=1):
        place = f"{idx}페이지"
        try:
            if await ingest_shuttle_image(place, url, idx, force=force) is not None:
                updated.append(place)
        except Exception:
            logger.exception("셔틀 시간표 동기화 실패: place=%s", place)
    return updated


async def save_user_timetable(place: str, trips: list[ParsedTrip]) -> dict:
    """사용자가 직접 입력한 시간표를 저장합니다. 기존 이미지 관련 필드는 있으면 보존합니다."""
    shuttle_trips = [
        ShuttleTrip(**t.model_dump(), **SHUTTLE_DIRECTIONS[t.direction]) for t in trips
    ]
    async with _lock:
        store = load_store()
        existing = store.get(place)
        doc = {
            "place": place,
            # 출처 정보(page/title/image_*)는 기존 AI 문서가 있으면 그대로 물려받는다
            "page": existing.get("page") if existing else None,
            "title": existing.get("title") if existing else None,
            "image_url": existing.get("image_url") if existing else None,
            "image_sha256": existing.get("image_sha256") if existing else None,
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
    - MM-DD 형식이 아닌 값(AI 오류)이 하나라도 섞이면, 데이터 유실보다 포함시키는 편이 안전하므로 True.
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
    중복을 제거합니다(개별 상세표가 통합본보다 store 순서상 앞서므로 먼저 나온 것을 유지).
    """
    # include_all이면 md를 쓰지 않는다. 아니면 호출 측이 날짜를 안 줘도 오늘(KST)로 채워
    # _is_valid_on에 None이 넘어가 비교에서 터지는 일이 없게 한다.
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
            if not include_all and not _is_valid_on(t.get("valid_from"), t.get("valid_to"), md):
                continue
            dedupe_key = (
                t["day_type"], t["direction"], t["departure_time"], t["departure_time_end"],
                t.get("valid_from"), t.get("valid_to"),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append({"place": p, **t})
    return out
