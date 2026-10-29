"""셔틀 시간표 원본 PDF 수집·파싱·검증과 저장소 동기화."""

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Config, logger
from app.config.shuttle_routes import SHUTTLE_DIRECTIONS
from app.schemas.shuttle import ShuttleTrip
from app.services.shuttle_schedule import recency, today
from app.services.shuttle_store import (
    load_store,
    save_store,
    store_lock,
    with_trip_ids,
)
from app.utils.ibookdownloader import BookDownloader
from app.utils.shuttle_pdf import parse_shuttle_pdf


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

    Args:
        force: True면 사용자 수정본·해시 가드를 무시하고 재파싱한다.

    Returns:
        저장한 문서. 건너뛰었으면 None.
    """
    place, pdf_url, content = await fetch_shuttle_pdf()

    async with store_lock:
        existing = load_store().get(place)
    if not force and existing is not None and existing.get("source") == "user":
        logger.info("셔틀 시간표 사용자 수정본이라 파싱 건너뜀: place=%s", place)
        return None

    pdf_sha256 = hashlib.sha256(content).hexdigest()
    known = (
        (
            existing.get("pdf_sha256"),
            (existing.get("pending") or {}).get("pdf_sha256"),
            existing.get("rejected_sha256"),  # 관리자가 거부한 파싱본
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
    trips = with_trip_ids(
        [
            ShuttleTrip(**t, **SHUTTLE_DIRECTIONS[t["direction"]]).model_dump()
            for t in parsed
        ]
    )
    doc: dict[str, Any] = {
        "place": place,
        "title": title,
        "pdf_url": pdf_url,
        "pdf_sha256": pdf_sha256,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "ref_date": today().isoformat(),
        "source": "pdf",
        "notes": notes,
        "trips": trips,
    }
    # force는 관리자가 PDF를 명시적으로 받아들이는 경로라 직전본으로 되돌리지 않습니다.
    good = None if force or not existing or existing.get("problems") else existing
    async with store_lock:
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
    async with store_lock:
        store = load_store()
        store[place] = doc
        save_store(store)
    logger.info("셔틀 시간표 저장: place=%s trips=%d", place, len(trips))
    return doc


def _latest_good(docs: list[dict]) -> dict | None:
    """검토 표시가 없는 문서 중 가장 최근 것(학기 교체 시 비교 대상).

    Args:
        docs: 후보 문서 목록.

    Returns:
        가장 최근의 정상 문서. 없으면 None.
    """
    good = [d for d in docs if not d.get("problems")]
    return max(good, key=recency) if good else None


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

    Args:
        trips: 파싱된 저장 trip 목록.
        days: 표 머리에서 읽은 운행일 단어 목록.
        previous: 비교할 직전 정상본. 없으면 None.
        same_place: previous가 같은 place의 문서인지.

    Returns:
        문제 설명 목록.
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
    """원본 PDF를 동기화합니다. 실패는 로그만 남기고 빈 목록을 반환합니다.

    Args:
        force: ingest_shuttle_pdf에 그대로 넘긴다.

    Returns:
        갱신된 place 목록.
    """
    try:
        doc = await ingest_shuttle_pdf(force=force)
    except Exception:
        logger.exception("셔틀 시간표 동기화 실패")
        return []
    return [doc["place"]] if doc is not None else []
