"""셔틀 시간표 엔드포인트의 OpenAPI responses 선언."""

from app.config import Config
from app.schemas.responses import AUTH_RESPONSES, OpenApiResponses

TIMETABLE_RESPONSES: OpenApiResponses = {
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
}

REFRESH_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.ACCEPTED: {
        "description": "iBook 원본 PDF로 백그라운드 동기화 시작(변경 시에만 재파싱). 결과는 GET /bus/timetable 로 확인"
    },
    **AUTH_RESPONSES,
}

PUT_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.OK: {
        "description": "사용자가 입력한 시간표를 저장했습니다. 이후 cron/refresh는 이 place를 덮어쓰지 않습니다."
    },
    **AUTH_RESPONSES,
}

DELETE_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.NO_CONTENT: {
        "description": "삭제했습니다. 이후 cron/refresh가 다시 PDF로 파싱합니다."
    },
    Config.HttpStatus.NOT_FOUND: {"description": "해당 place가 없습니다."},
    **AUTH_RESPONSES,
}
