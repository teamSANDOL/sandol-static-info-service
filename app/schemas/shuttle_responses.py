"""셔틀 시간표 엔드포인트의 OpenAPI responses 선언."""

from app.config import Config
from app.schemas.responses import SHUTTLE_AUTH_RESPONSES, OpenApiResponses

TIMETABLE_EXAMPLE = {
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


TIMETABLE_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.OK: {
        "description": (
            "date(기본 오늘, KST) 하루치 셔틀 시간표. day_type은 공휴일 정적 파일로 "
            "확정하고, 방향 6개(main_out·main_in·campus2_out·campus2_in·"
            "campus2_from_station·campus2_to_station)를 운행이 없어도 모두 담습니다. "
            "segments는 fixed(정시 1편)·frequent(수시운행 구간)·"
            "arrival_board(도착버스 탑승 구간, end=막차)이며 시작 시각 오름차순입니다."
        ),
        "content": {Config.Accept.JSON: {"example": TIMETABLE_EXAMPLE}},
    },
    Config.HttpStatus.NOT_MODIFIED: {
        "description": "If-None-Match가 현재 version과 같습니다(본문 없음)."
    },
    Config.HttpStatus.SERVICE_UNAVAILABLE: {
        "description": "저장된 시간표가 아예 없습니다(timetable_unavailable)."
    },
}

REFRESH_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.ACCEPTED: {
        "description": "iBook 원본 PDF로 백그라운드 동기화 시작(변경 시에만 재파싱). 결과는 GET /bus/timetable 로 확인"
    },
    **SHUTTLE_AUTH_RESPONSES,
}

PUT_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.OK: {
        "description": "사용자가 입력한 시간표를 저장했습니다. 이후 cron/refresh는 이 place를 덮어쓰지 않습니다."
    },
    **SHUTTLE_AUTH_RESPONSES,
}

DELETE_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.NO_CONTENT: {
        "description": "삭제했습니다. 이후 cron/refresh가 다시 PDF로 파싱합니다."
    },
    Config.HttpStatus.NOT_FOUND: {"description": "해당 place가 없습니다."},
    **SHUTTLE_AUTH_RESPONSES,
}
