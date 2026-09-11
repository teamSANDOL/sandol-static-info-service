import asyncio
import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.config import Config, logger
from app.schemas.shuttle import DayType, ParsedTimetable
from app.utils import BookDownloader
from app.utils.api_key import verify_api_key
from app.utils.image_response import build_image_response
from app.utils.shuttle_timetable import (
    delete_timetable,
    load_store,
    query_trips,
    save_user_timetable,
    sync_shuttle_timetables,
)

router = APIRouter(prefix="/bus")

KST = ZoneInfo("Asia/Seoul")


@router.get(
    "/images",
    responses={
        Config.HttpStatus.OK: {
            "description": "모든 버스 이미지 반환 (응답 타입에 따라 json/base64/zip 등 다양하게 반환됩니다)",
            "content": {
                Config.Accept.JSON: {
                    "example": {
                        "image_urls": [
                            "https://example.com/img1.jpg",
                            "https://example.com/img2.jpg",
                        ]
                    }
                },
                Config.Accept.BASE64: {
                    "example": {
                        "image_base64_list": [
                            "/9j/4AAQSkZJRgAB...",
                            "/9j/4AAQSkZJRgAC...",
                        ]
                    }
                },
                Config.Accept.ZIP: {"schema": {"type": "string", "format": "binary"}},
                Config.Accept.OCTET_STREAM: {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        },
        Config.HttpStatus.NOT_FOUND: {"description": "버스 이미지가 없습니다."},
        Config.HttpStatus.NOT_ACCEPTABLE: {
            "description": "지원되지 않는 Accept 헤더입니다."
        },
    },
    response_class=Response,
)
async def get_all_bus_images(request: Request):
    """모든 버스 이미지들을 Accept 헤더에 따라 다양한 형식으로 반환합니다."""
    accept_header = request.headers.get("accept", "").lower()
    logger.info("모든 버스 이미지 요청 수신")

    # MIME 타입에 따라 response_type 결정
    if Config.Accept.JSON in accept_header:
        response_type = "json"
    elif Config.Accept.BASE64 in accept_header:
        response_type = "base64"
    elif Config.Accept.ZIP in accept_header:
        response_type = "zip"
    elif Config.Accept.OCTET_STREAM in accept_header:
        response_type = "octet-stream"
    else:
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_ACCEPTABLE,
            detail="지원되지 않는 Accept 헤더입니다.",
        )

    downloader = BookDownloader(Config.SHUTTLE_URL)
    image_urls = await downloader.fetch_image_list()

    if not image_urls:
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_FOUND, detail="버스 이미지가 없습니다."
        )

    return await build_image_response(image_urls, response_type)


@router.get(
    "/image/{index}",
    responses={
        Config.HttpStatus.OK: {
            "description": "특정 인덱스의 버스 이미지 반환 (Accept 헤더에 따라 포맷이 달라짐)",
            "content": {
                Config.Accept.JSON: {
                    "example": {"image_url": "https://example.com/img1.jpg"}
                },
                Config.Accept.BASE64: {
                    "example": {"image_base64": "/9j/4AAQSkZJRgAB..."}
                },
                Config.Accept.OCTET_STREAM: {
                    "schema": {"type": "string", "format": "binary"}
                },
                Config.ImageType.JPEG: {
                    "schema": {"type": "string", "format": "binary"}
                },
                "text/plain": {"example": "https://example.com/img1.jpg"},
            },
        },
        Config.HttpStatus.NOT_FOUND: {"description": "해당 index의 이미지가 없습니다."},
        Config.HttpStatus.NOT_ACCEPTABLE: {
            "description": "지원되지 않는 Accept 헤더입니다."
        },
    },
    response_class=Response,
)
async def get_bus_image_by_index(index: int, request: Request):
    """특정 인덱스(1부터 시작)의 버스 이미지를 Accept 헤더에 따라 다양한 형식으로 반환합니다."""
    accept_header = request.headers.get("accept", "").lower()
    logger.info(f"버스 이미지 요청 (index={index})")

    if Config.Accept.JSON in accept_header:
        response_type = "json"
    elif Config.Accept.BASE64 in accept_header:
        response_type = "base64"
    elif Config.Accept.OCTET_STREAM in accept_header:
        response_type = "octet-stream"
    elif Config.Accept.ZIP in accept_header:
        response_type = "zip"
    elif "text/plain" in accept_header:
        response_type = "text"
    elif Config.ImageType.JPEG in accept_header:
        response_type = "jpeg"
    else:
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_ACCEPTABLE,
            detail="지원되지 않는 Accept 헤더입니다.",
        )

    downloader = BookDownloader(Config.SHUTTLE_URL)
    image_urls = await downloader.fetch_image_list()

    if index < 1 or index > len(image_urls):
        raise HTTPException(
            status_code=Config.HttpStatus.NOT_FOUND,
            detail="해당 index의 이미지가 없습니다.",
        )

    return await build_image_response(image_urls[index - 1], response_type)


@router.get(
    "/timetable",
    responses={
        Config.HttpStatus.OK: {
            "description": (
                "AI로 파싱한 셔틀버스 시간표. route(부분 일치)/day_type/origin/destination"
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
                                "place": "2페이지",
                                "page": 2,
                                "title": "여름방학 (정상근무) 셔틀버스 시간표",
                                "image_url": "https://...",
                                "parsed_at": "2026-09-07T00:00:00+00:00",
                            }
                        ],
                        "trips": [
                            {
                                "place": "2페이지",
                                "route": "정왕역 ↔ 본교",
                                "direction": "정왕역 → 본교",
                                "origin": "정왕역",
                                "destination": "본교",
                                "via": [],
                                "day_type": "평일",
                                "day_note": None,
                                "departure_time": "22:17",
                                "departure_time_end": None,
                                "valid_from": "08-25",
                                "valid_to": "08-31",
                                "period_note": "정상근무 8.25~8.31",
                                "boarding_place": "파리바게뜨 건너편 버스정류장",
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

    place는 이벤트 출처(iBook 페이지 번호) 이름일 뿐이며 한 이미지에 여러 노선이 있을 수 있으므로
    노선 필터는 route로 합니다.

    기본은 오늘(KST) 기준 운행 기간(valid_from/valid_to)에 해당하는 trip만 반환합니다.
    date 쿼리로 다른 날짜를 기준으로 조회하거나, all=true로 기간 필터 없이 전체를 조회할 수 있습니다.
    방학 중엔 통합표와 개별 기간표가 같은 내용을 중복으로 담고 있을 수 있어
    (운행일, 방향, 시각, 운행 기간) 기준으로 중복을 제거해 반환합니다.
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
                for k in ("place", "page", "title", "image_url", "parsed_at", "source", "updated_at")
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
    dependencies=[Depends(verify_api_key)],
    responses={
        Config.HttpStatus.ACCEPTED: {
            "description": "iBook의 현재 이미지 목록으로 백그라운드 동기화 시작(변경분만 재파싱). 결과는 GET /bus/timetable 로 확인"
        },
        Config.HttpStatus.UNAUTHORIZED: {"description": "X-API-Key 헤더가 없거나 일치하지 않습니다"},
    },
)
async def refresh_shuttle_timetable(force: bool = False):
    """수동으로 셔틀 시간표 동기화를 트리거합니다. 평소엔 6시간 cron이 대신합니다.

    flex tier 파싱은 수 분~수십 분 걸릴 수 있어 게이트웨이 타임아웃을 피하려고 백그라운드로 돌립니다.
    force=True면 사용자 수정본과 이미지 해시 가드를 모두 무시하고 AI로 재파싱합니다
    (사용자 수정본을 덮어쓰는 유일한 경로이며, 명시적으로 호출했을 때만 동작).
    인증: X-API-Key 헤더 필요.
    """
    asyncio.create_task(sync_shuttle_timetables(force))
    return {"queued": True}

@router.put(
    "/timetable/{place}",
    dependencies=[Depends(verify_api_key)],
    responses={
        Config.HttpStatus.OK: {"description": "사용자가 입력한 시간표를 저장했습니다. 이후 cron/refresh는 이 place를 AI로 덮어쓰지 않습니다."},
        Config.HttpStatus.UNAUTHORIZED: {"description": "X-API-Key 헤더가 없거나 일치하지 않습니다"},
    },
)
async def put_shuttle_timetable(place: str, body: ParsedTimetable):
    """place의 시간표를 사용자가 직접 입력한 내용으로 덮어씁니다.

    저장 문서는 source="user"로 표시되어 cron/refresh(force 없이는)가 다시 파싱하지 않습니다.
    인증: X-API-Key 헤더 필요.
    """
    return await save_user_timetable(place, body.trips)


@router.delete(
    "/timetable/{place}",
    status_code=Config.HttpStatus.NO_CONTENT,
    dependencies=[Depends(verify_api_key)],
    responses={
        Config.HttpStatus.NO_CONTENT: {"description": "삭제했습니다. 이후 cron/refresh가 다시 AI로 파싱합니다."},
        Config.HttpStatus.UNAUTHORIZED: {"description": "X-API-Key 헤더가 없거나 일치하지 않습니다"},
        Config.HttpStatus.NOT_FOUND: {"description": "해당 place가 없습니다."},
    },
)
async def delete_shuttle_timetable(place: str):
    """place 문서를 삭제합니다. 사용자 수정본을 되돌려 다시 AI 파싱 대상으로 만들 때 사용합니다.

    인증: X-API-Key 헤더 필요.
    """
    if not await delete_timetable(place):
        raise HTTPException(status_code=Config.HttpStatus.NOT_FOUND)
