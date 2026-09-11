"""셔틀버스 노선/방향 상수.

iBook 셔틀 시간표(2페이지)에 있는 방향은 이것이 전부입니다. AI는 아래 키 중 하나만 고르고,
노선명·출발지·경유지·도착지는 여기서 채웁니다. 학교가 노선을 바꾸면 이 표와
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
