"""셔틀버스 라우터.

- 이미지: GET /bus/images, GET /bus/image/{index} (iBook 뷰어 페이지 이미지)
- 시간표: GET /bus/timetable, POST /bus/timetable/refresh,
          PUT/DELETE /bus/timetable/{place}
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query

from app.config import Config
from app.routers.ibook_images import create_ibook_image_router
from app.schemas.shuttle import DayType, ParsedTimetable
from app.utils.shuttle_timetable import (
    delete_timetable,
    load_store,
    query_trips,
    save_user_timetable,
    sync_shuttle_timetables,
)

KST = ZoneInfo("Asia/Seoul")

router = create_ibook_image_router("/bus", Config.SHUTTLE_URL, "버스")


@router.get(
    "/timetable",
    responses={
        Config.HttpStatus.OK: {
            "description": (
                "iBook 원본 PDF에서 파싱한 셔틀버스 시간표. "
                "route(부분 일치)/day_type/origin/destination"
                "(정왕역·본교·제2캠퍼스, 경유지 포함) 쿼리로 필터. "
                "기본 동작은 오늘(KST) 기준 유효 기간(valid_from~valid_to) 안에 있는 trip만 반환하며, "
                "date로 다른 날짜를 지정하거나 all=true로 기간 필터 없이 전체를 볼 수 있습니다. "
                "방학 등 기간이 겹치는 통합표와 개별표의 중복은 자동 제거됩니다."
            ),
            "content": {
                Config.Accept.JSON: {
                    "example": {
                        "sources": [
                            {
                                "place": "26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22",
                                "title": "◈ 2학기 셔틀버스 시간표 ◈",
                                "pdf_url": "https://contents.tukorea.ac.kr/...",
                                "parsed_at": "2026-09-07T00:00:00+00:00",
                                "source": "pdf",
                            }
                        ],
                        "trips": [
                            {
                                "place": "26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22",
                                "route": "정왕역 ↔ 본교",
                                "direction": "정왕역 → 본교",
                                "origin": "정왕역",
                                "destination": "본교",
                                "via": [],
                                "day_type": "평일",
                                "day_note": None,
                                "departure_time": "22:17",
                                "departure_time_end": None,
                                "valid_from": "09-01",
                                "valid_to": "12-22",
                                "period_note": None,
                                "boarding_place": None,
                                "note": "막차",
                            }
                        ],
                    }
                }
            },
        },
        Config.HttpStatus.NOT_FOUND: {"description": "파싱된 시간표가 아직 없습니다."},
    },
)
async def get_shuttle_timetable(
    route: str | None = None,
    day_type: DayType | None = None,
    origin: str | None = None,
    destination: str | None = None,
    date: datetime.date | None = None,
    include_all: bool = Query(False, alias="all"),
):
    """저장된 셔틀버스 시간표를 반환합니다. route는 부분 일치, origin/destination은 경유지 포함 일치.

    place는 출처 문서 이름일 뿐이며 한 PDF에 여러 노선이 있으므로 노선 필터는 route로 합니다.

    기본은 오늘(KST) 기준 운행 기간(valid_from/valid_to)에 해당하는 trip만 반환합니다.
    date 쿼리로 다른 날짜를 기준으로 조회하거나, all=true로 기간 필터 없이 전체를 조회할 수 있습니다.
    """
    store = load_store()
    if not store:
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_FOUND,
            detail="파싱된 시간표가 아직 없습니다.",
        )
    query_date = date or datetime.datetime.now(KST).date()
    return {
        "sources": [
            {
                k: d.get(k)
                for k in ("place", "title", "pdf_url", "parsed_at", "source", "updated_at")
            }
            for d in store.values()
        ],
        "trips": query_trips(
            store, route, day_type, origin, destination, query_date, include_all=include_all
        ),
    }


@router.post(
    "/timetable/refresh",
    status_code=Config.HttpStatus.ACCEPTED,
    responses={
        Config.HttpStatus.ACCEPTED: {
            "description": "iBook 원본 PDF로 백그라운드 동기화 시작(변경 시에만 재파싱). 결과는 GET /bus/timetable 로 확인"
        },
    },
)
async def refresh_shuttle_timetable(force: bool = False):
    """수동으로 셔틀 시간표 동기화를 트리거합니다. 평소엔 6시간 cron이 대신합니다.

    PDF 파싱은 빠르지만 다운로드가 느릴 수 있어 백그라운드로 돌립니다.
    force=True면 사용자 수정본과 PDF 해시 가드를 모두 무시하고 재파싱합니다
    (사용자 수정본을 덮어쓰는 유일한 경로이며, 명시적으로 호출했을 때만 동작).
    """
    asyncio.create_task(sync_shuttle_timetables(force))
    return {"queued": True}


@router.put(
    "/timetable/{place}",
    responses={
        Config.HttpStatus.OK: {
            "description": "사용자가 입력한 시간표를 저장했습니다. 이후 cron/refresh는 이 place를 덮어쓰지 않습니다."
        },
    },
)
async def put_shuttle_timetable(place: str, body: ParsedTimetable):
    """place의 시간표를 사용자가 직접 입력한 내용으로 덮어씁니다.

    저장 문서는 source="user"로 표시되어 cron/refresh(force 없이는)가 다시 파싱하지 않습니다.
    """
    return await save_user_timetable(place, body.trips)


@router.delete(
    "/timetable/{place}",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={
        Config.HttpStatus.NO_CONTENT: {
            "description": "삭제했습니다. 이후 cron/refresh가 다시 PDF로 파싱합니다."
        },
        Config.HttpStatus.NOT_FOUND: {"description": "해당 place가 없습니다."},
    },
)
async def delete_shuttle_timetable(place: str):
    """place 문서를 삭제합니다. 사용자 수정본을 되돌려 다시 PDF 파싱 대상으로 만들 때 사용합니다."""
    if not await delete_timetable(place):
        raise HTTPException(status_code=Config.HttpStatus.NOT_FOUND)
