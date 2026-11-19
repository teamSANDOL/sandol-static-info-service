"""공공데이터포털 특일 정보(getRestDeInfo)로 공휴일을 받아 연 단위로 캐시합니다.

요청 경로는 네트워크를 타지 않습니다. 기동 시와 하루 한 번(스케줄러)만 백그라운드에서
올해·내년을 받아 `DATA_DIR/holidays/<연도>.json`에 저장하고, 요청은 메모리·디스크
캐시만 읽습니다. HOLIDAY_API_KEY가 없으면 아무것도 하지 않습니다(정적 파일만 사용).
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from app.config import Config, logger

API_URL = (
    "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"
)
TIMEOUT_SECONDS = 3.0
PAGE_SIZE = 100
MAX_PAGES = 5
REFRESH_SECONDS = 24 * 60 * 60  # 올해·내년 캐시를 다시 받는 최소 간격

# httpx는 INFO로 쿼리까지 든 URL(=서비스키)을 찍으므로 앱 로그 설정과 무관하게 막는다.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

_memory: dict[int, dict[str, str]] = {}
_state = {"loaded": False}  # 디스크 캐시를 메모리로 한 번 읽었는지
_lock = asyncio.Lock()  # 같은 연도를 동시에 받지 않도록 갱신 전체를 직렬화


def today_year() -> int:
    """오늘의 연도(KST)."""
    return datetime.now(ZoneInfo("Asia/Seoul")).year


def _cache_path(year: int) -> str:
    return os.path.join(Config.DATA_DIR, "holidays", f"{year}.json")


def _read_cache(year: int) -> dict | None:
    try:
        with open(_cache_path(year), encoding="utf-8") as f:
            doc = json.load(f)
        if isinstance(doc.get("dates"), dict) and isinstance(
            doc.get("fetched_at"), (int, float)
        ):
            return doc
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _write_cache(year: int, dates: dict[str, str]) -> None:
    path = _cache_path(year)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "dates": dates}, f, ensure_ascii=False)
    os.replace(tmp, path)


def api_holidays() -> dict[str, str]:
    """캐시된 API 공휴일 전체(날짜 ISO → 이름). 네트워크를 타지 않습니다.

    Returns:
        캐시가 없으면 빈 dict.
    """
    if not _state["loaded"]:
        _state["loaded"] = True
        folder = os.path.dirname(_cache_path(0))
        for name in os.listdir(folder) if os.path.isdir(folder) else []:
            year = name.removesuffix(".json")
            doc = _read_cache(int(year)) if year.isdigit() else None
            if doc:
                _memory.setdefault(int(year), doc["dates"])
    return {d: n for year in sorted(_memory) for d, n in _memory[year].items()}


def _parse_page(payload: dict) -> tuple[dict[str, str], int]:
    """응답 JSON 한 쪽 → (공휴일, totalCount). 형식이 다르면 ValueError."""
    try:
        response = payload["response"]
        if response["header"]["resultCode"] != "00":
            raise ValueError(f"resultCode={response['header']['resultCode']}")
        body = response["body"]
        total = int(body["totalCount"])
        items = body.get("items")
        # 0건이면 items가 빈 문자열, 1건이면 item이 배열이 아닌 객체다.
        rows = items.get("item", []) if items else []
        if isinstance(rows, dict):
            rows = [rows]
        dates: dict[str, str] = {}
        for row in rows:
            if row["isHoliday"] != "Y":
                continue
            d = str(row["locdate"])
            dates[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = str(row["dateName"])
        return dates, total
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"unexpected shape: {type(exc).__name__}") from exc


async def fetch_year(client: httpx.AsyncClient, year: int, key: str) -> dict[str, str]:
    """한 해 공휴일을 전부 받습니다(totalCount가 크면 다음 쪽도).

    Args:
        client: HTTP 클라이언트.
        year: 연도.
        key: 서비스키(인코딩된 값도 가능).

    Returns:
        날짜 ISO → 이름. 자료가 없으면 빈 dict.

    Raises:
        ValueError: HTTP 오류·JSON이 아닌 본문·형식 오류. 메시지에 키·URL은 없다.
        httpx.HTTPError: 연결·타임아웃 오류.
    """
    dates: dict[str, str] = {}
    for page in range(1, MAX_PAGES + 1):
        res = await client.get(
            API_URL,
            params={
                # 포털의 인코딩된 키(% 포함)를 넣어도 httpx가 한 번만 인코딩하게 먼저 푼다.
                "ServiceKey": unquote(key),
                "solYear": f"{year:04d}",
                "_type": "json",
                "numOfRows": PAGE_SIZE,
                "pageNo": page,
            },
        )
        if res.status_code != Config.HttpStatus.OK:
            raise ValueError(f"HTTP {res.status_code}")
        try:
            payload = res.json()
        except ValueError as exc:  # XML 오류 본문 등
            raise ValueError("response is not JSON") from exc
        got, total = _parse_page(payload)
        dates.update(got)
        if page * PAGE_SIZE >= total:
            break
    return dates


def _stale(year: int, now_year: int) -> bool:
    doc = _read_cache(year)
    if doc is None:
        return True
    if year < now_year:  # 과거 연도는 한 번 받으면 다시 받지 않는다
        return False
    return time.time() - doc["fetched_at"] >= REFRESH_SECONDS


async def refresh_holidays() -> None:
    """올해·내년 공휴일을 받아 캐시합니다. 실패는 경고만 남기고 기존 캐시를 유지합니다."""
    key = Config.HOLIDAY_API_KEY
    if not key:
        return
    this_year = today_year()
    async with _lock:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            for year in (this_year, this_year + 1):
                if not _stale(year, this_year):
                    continue
                try:
                    dates = await fetch_year(client, year, key)
                except (ValueError, httpx.HTTPError, OSError) as exc:
                    # 예외 문자열에 URL(키)이 들어갈 수 있어 종류만 남긴다.
                    logger.warning(
                        "공휴일 API 실패(%d년): %s", year, type(exc).__name__
                    )
                    continue
                if not dates:  # 아직 자료가 없는 해: 캐시하지 않고 기존 판정으로 둔다
                    logger.info("공휴일 API에 %d년 자료가 없습니다", year)
                    continue
                try:
                    _write_cache(year, dates)
                except OSError as exc:
                    logger.warning("공휴일 캐시 저장 실패: %s", type(exc).__name__)
                _memory[year] = dates
