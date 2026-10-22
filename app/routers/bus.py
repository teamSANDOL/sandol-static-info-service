"""셔틀버스 라우터.

- 이미지: GET /bus/images, GET /bus/image/{index} (iBook 뷰어 페이지 이미지)
- 시간표: GET /bus/timetable, POST /bus/timetable/refresh,
          PUT/DELETE /bus/timetable/{place}
- 쓰기(refresh/PUT/DELETE)는 Keycloak 관리자 Bearer 토큰이 필요합니다(require_admin).
"""

import asyncio
import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, Header, HTTPException, Response

from app.config import Config, logger
from app.routers.ibook_images import create_ibook_image_router
from app.schemas.shuttle import ParsedTimetable
from app.utils.shuttle_timetable import (
    build_timetable,
    delete_timetable,
    load_store,
    needs_review,
    pick_document,
    save_user_timetable,
    sync_shuttle_timetables,
)
from app.utils.keycloak_auth import require_admin

KST = ZoneInfo("Asia/Seoul")
Admin = Annotated[str, Depends(require_admin)]
_background: set[asyncio.Task] = set()  # create_task 결과가 GC되지 않게 참조 보관

_AUTH_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Bearer 토큰이 없거나 유효하지 않습니다."},
    403: {"description": "관리자 롤이 없습니다."},
    503: {"description": "관리자 인증이 설정되지 않았습니다(fail-closed)."},
}


def _audit(sub: str, action: str, target: str) -> None:
    """관리자 화면의 log_admin_action과 같은 형식의 감사 로그(Bearer 경로용)."""
    logger.info("admin_audit sub=%s action=%s target=%s", sub, action, target)


router = create_ibook_image_router("/bus", Config.SHUTTLE_URL, "버스")


_CACHE_CONTROL = "public, max-age=300"
_EXAMPLE = {
    "date": "2026-10-22",
    "day_type": "weekday",
    "holiday_name": None,
    "version": "3f1c0a9b8d7e6f50",
    "valid_from": "2026-09-01",
    "valid_to": "2026-12-22",
    "source": {
        "title": "26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22",
        "pdf_url": "https://contents.tukorea.ac.kr/...",
        "parsed_at": "2026-10-21T00:00:00+00:00",
        "origin": "pdf",
        "needs_review": False,
    },
    "stops": [
        {
            "id": "jeongwang",
            "name": "정왕역",
            "lat": 37.3512712,
            "lng": 126.7411157,
            "boarding_note": "정왕역 출발은 꽃집 앞",
        }
    ],
    "routes": [
        {
            "id": "main",
            "name": "정왕역 ↔ 본교",
            "directions": [
                {
                    "id": "main_in",
                    "from": "jeongwang",
                    "to": "main_campus",
                    "via": [],
                    "segments": [
                        {
                            "type": "frequent",
                            "start": "08:40",
                            "end": "10:00",
                            "note": "수시운행",
                        },
                        {"type": "fixed", "time": "10:00", "note": None},
                        {
                            "type": "arrival_board",
                            "start": "17:00",
                            "end": "22:17",
                            "note": "도착버스 탑승",
                        },
                    ],
                }
            ],
        }
    ],
    "notes": ["소요시간 10분, 퇴근(혼잡) 시간대는 20~30분 소요 예상"],
}


@router.get(
    "/timetable",
    responses={
        Config.HttpStatus.OK: {
            "description": (
                "date(기본 오늘, KST) 하루치 셔틀 시간표. day_type은 공휴일 정적 파일로 "
                "확정하고, 방향 6개(main_out·main_in·campus2_out·campus2_in·"
                "campus2_from_station·campus2_to_station)를 운행이 없어도 모두 담습니다. "
                "segments는 fixed(정시 1편)·frequent(수시운행 구간)·"
                "arrival_board(도착버스 탑승 구간, end=막차)이며 시작 시각 오름차순입니다."
            ),
            "content": {Config.Accept.JSON: {"example": _EXAMPLE}},
        },
        Config.HttpStatus.NOT_MODIFIED: {
            "description": "If-None-Match가 현재 version과 같습니다(본문 없음)."
        },
        Config.HttpStatus.SERVICE_UNAVAILABLE: {
            "description": "저장된 시간표가 아예 없습니다(timetable_unavailable)."
        },
    },
)
async def get_shuttle_timetable(
    response: Response,
    date: datetime.date | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
):
    """조회일 하루치 셔틀 시간표를 모바일이 해석 없이 그릴 수 있는 형태로 반환합니다.

    응답에 `ETag: "<version>"`와 `Cache-Control: public, max-age=300`을 붙이고,
    `If-None-Match`가 현재 태그와 같으면 본문 없이 304를 돌려줍니다. date를 생략하면
    태그는 `"<version>-<YYYYMMDD(KST)>"`입니다.
    """
    store = load_store()
    if not store:
        raise HTTPException(
            status_code=Config.HttpStatus.SERVICE_UNAVAILABLE,
            detail="timetable_unavailable",
        )
    query_date = date or datetime.datetime.now(KST).date()
    doc = pick_document(store, query_date)
    body = build_timetable(doc, query_date, needs_review=needs_review(doc))
    # version은 날짜와 무관하므로 date 없이(=오늘) 부르면 날짜를 태그에 섞어야
    # 다음 날 재검증에서 어제 본문이 304로 남지 않습니다. 앱은 date를 항상 보냅니다.
    tag = body["version"] if date else f"{body['version']}-{query_date:%Y%m%d}"
    headers = {"ETag": f'"{tag}"', "Cache-Control": _CACHE_CONTROL}
    if _etag_matches(if_none_match, tag):
        return Response(status_code=Config.HttpStatus.NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return body


def _etag_matches(if_none_match: str | None, version: str) -> bool:
    """If-None-Match(쉼표 목록, W/ 약한 태그, *)에 현재 version이 있는지."""
    tags = {t.strip().removeprefix("W/") for t in (if_none_match or "").split(",")}
    return "*" in tags or f'"{version}"' in tags


@router.post(
    "/timetable/refresh",
    status_code=Config.HttpStatus.ACCEPTED,
    responses={
        Config.HttpStatus.ACCEPTED: {
            "description": "iBook 원본 PDF로 백그라운드 동기화 시작(변경 시에만 재파싱). 결과는 GET /bus/timetable 로 확인"
        },
        **_AUTH_RESPONSES,
    },
)
async def refresh_shuttle_timetable(sub: Admin, force: bool = False):
    """수동으로 셔틀 시간표 동기화를 트리거합니다. 평소엔 6시간 cron이 대신합니다.

    PDF 파싱은 빠르지만 다운로드가 느릴 수 있어 백그라운드로 돌립니다.
    force=True면 사용자 수정본과 PDF 해시 가드를 모두 무시하고 재파싱합니다
    (사용자 수정본을 덮어쓰는 유일한 경로이며, 명시적으로 호출했을 때만 동작).
    """
    _audit(sub, "shuttle_refresh", f"force={force}")
    task = asyncio.create_task(sync_shuttle_timetables(force))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"queued": True}


@router.put(
    "/timetable/{place}",
    responses={
        Config.HttpStatus.OK: {
            "description": "사용자가 입력한 시간표를 저장했습니다. 이후 cron/refresh는 이 place를 덮어쓰지 않습니다."
        },
        **_AUTH_RESPONSES,
    },
)
async def put_shuttle_timetable(place: str, body: ParsedTimetable, sub: Admin):
    """place의 시간표를 사용자가 직접 입력한 내용으로 덮어씁니다.

    저장 문서는 source="user"로 표시되어 cron/refresh(force 없이는)가 다시 파싱하지 않습니다.
    """
    doc = await save_user_timetable(
        place, body.trips, body.title, updated_by=sub, notes=body.notes
    )
    _audit(sub, "shuttle_put", place)
    return doc


@router.delete(
    "/timetable/{place}",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={
        Config.HttpStatus.NO_CONTENT: {
            "description": "삭제했습니다. 이후 cron/refresh가 다시 PDF로 파싱합니다."
        },
        Config.HttpStatus.NOT_FOUND: {"description": "해당 place가 없습니다."},
        **_AUTH_RESPONSES,
    },
)
async def delete_shuttle_timetable(place: str, sub: Admin):
    """place 문서를 삭제합니다. 사용자 수정본을 되돌려 다시 PDF 파싱 대상으로 만들 때 사용합니다."""
    if not await delete_timetable(place):
        raise HTTPException(status_code=Config.HttpStatus.NOT_FOUND)
    _audit(sub, "shuttle_delete", place)
