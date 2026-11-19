"""셔틀버스 노선/방향 상수.

iBook 셔틀 시간표 PDF에 있는 방향은 이것이 전부입니다. 파서는 아래 키 중 하나로만 방향을
확정하고, 노선명·출발지·경유지·도착지는 여기서 채웁니다. 학교가 노선을 바꾸면 이 표와
app/schemas/shuttle.py 의 Direction Literal을 같이 수정합니다.
"""

ROUTE_MAIN = "정왕역 ↔ 본교"
ROUTE_SECOND = "제2캠퍼스 ↔ 본교·정왕역"

SHUTTLE_DIRECTIONS: dict[str, dict] = {
    "본교 → 정왕역": {
        "route": ROUTE_MAIN,
        "origin": "본교",
        "via": [],
        "destination": "정왕역",
    },
    "정왕역 → 본교": {
        "route": ROUTE_MAIN,
        "origin": "정왕역",
        "via": [],
        "destination": "본교",
    },
    "본교 → 제2캠퍼스": {
        "route": ROUTE_SECOND,
        "origin": "본교",
        "via": [],
        "destination": "제2캠퍼스",
    },
    "제2캠퍼스 → 본교": {
        "route": ROUTE_SECOND,
        "origin": "제2캠퍼스",
        "via": [],
        "destination": "본교",
    },
    "정왕역 → 본교 → 제2캠퍼스": {
        "route": ROUTE_SECOND,
        "origin": "정왕역",
        "via": ["본교"],
        "destination": "제2캠퍼스",
    },
    "제2캠퍼스 → 본교 → 정왕역": {
        "route": ROUTE_SECOND,
        "origin": "제2캠퍼스",
        "via": ["본교"],
        "destination": "정왕역",
    },
}

# 모바일 응답(GET /bus/timetable)용 고정 id. 앱이 이 값으로 화면을 고르므로 바꾸지 않습니다.
STOP_IDS = {"본교": "main_campus", "정왕역": "jeongwang", "제2캠퍼스": "campus2"}
ROUTE_IDS = {ROUTE_MAIN: "main", ROUTE_SECOND: "campus2"}
# 응답에 나오는 순서이기도 합니다.
DIRECTION_IDS = {
    "본교 → 정왕역": "main_out",
    "정왕역 → 본교": "main_in",
    "본교 → 제2캠퍼스": "campus2_out",
    "제2캠퍼스 → 본교": "campus2_in",
    "정왕역 → 본교 → 제2캠퍼스": "campus2_from_station",
    "제2캠퍼스 → 본교 → 정왕역": "campus2_to_station",
}
