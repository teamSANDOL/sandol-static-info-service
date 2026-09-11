"""셔틀버스 시간표 스키마. OpenAI structured output 및 API 응답에 공용으로 사용합니다."""

from typing import Literal

from pydantic import BaseModel, Field

DayType = Literal["평일", "토요일", "일요일", "주말", "공휴일"]

# app/config/shuttle_routes.py 의 SHUTTLE_DIRECTIONS 키와 동일해야 함 (tests에서 검증)
Direction = Literal[
    "본교 → 정왕역",
    "정왕역 → 본교",
    "본교 → 제2캠퍼스",
    "제2캠퍼스 → 본교",
    "정왕역 → 본교 → 제2캠퍼스",
    "제2캠퍼스 → 본교 → 정왕역",
]


class ParsedTrip(BaseModel):
    """AI가 이미지에서 추출하는 출발 1건. 노선/출발지/도착지는 방향 상수에서 채워지므로 여기 없음."""

    direction: Direction = Field(
        description=(
            "운행 방향. '학교 출발(하교)' 열은 '본교 → 정왕역', '정왕역 출발(등교)' 열은 '정왕역 → 본교'. "
            "'정왕역출발' 표기가 있는 제2캠퍼스행은 '정왕역 → 본교 → 제2캠퍼스'"
        )
    )
    day_type: DayType = Field(description="운행일 구분")
    day_note: str | None = Field(
        description="운행일 표기 옆 괄호 안 텍스트만. 예: '토요일(일학습병행학부)' → '일학습병행학부'. 각주/안내문은 넣지 말 것. 없으면 null"
    )
    departure_time: str = Field(
        description="출발 시각 HH:MM (24시간). 시간 범위면 시작 시각"
    )
    departure_time_end: str | None = Field(
        description="수시운행 등 시간 범위일 때 종료 시각 HH:MM. 단일 시각이면 null"
    )
    valid_from: str | None = Field(
        description=(
            "이 출발 건이 속한 표의 운행 시작일 MM-DD. 제목 옆/표 위·우측의 기간 표기"
            "(예: '★★ 8.25 ~ 8.31 ★★', '7.14 ~ 8.24')에서 채움. 통합표(행마다 계절학기/단축근무/"
            "정상근무 라벨로 기간이 다름)면 그 행 라벨에 해당하는 기간을 씀. "
            "이미지에 연도 표기가 없으므로 연도는 절대 쓰지 말 것(MM-DD만). "
            "기간 표기가 전혀 없는 상시 시간표면 null"
        )
    )
    valid_to: str | None = Field(
        description="운행 종료일 MM-DD. valid_from과 동일한 규칙. 기간 표기가 없으면 null"
    )
    period_note: str | None = Field(
        description=(
            "운행 기간/근무형태 원문 표기 근거 보존용. 예: '정상근무 8.25~8.31', '계절학기'. "
            "통합표면 그 행의 라벨(계절학기/단축근무/정상근무 등)을 씀. 기간 표기가 없으면 null"
        )
    )
    boarding_place: str | None = Field(
        description="탑승 장소. 예: '정왕역 꽃집 앞', '본교 운동장 옆(서문) 버스정류장'. 없으면 null"
    )
    note: str | None = Field(
        description="이 출발 건에만 해당하는 비고. 예: '막차', '수시운행', '오이도역 도착'. 운행기간 같은 표 전체 공통 정보는 넣지 말 것. 없으면 null"
    )


class ParsedTimetable(BaseModel):
    """AI structured output 스키마."""

    title: str | None = Field(
        description=(
            "이미지 상단 제목 원문. 예: '여름방학 (정상근무) 셔틀버스 시간표', "
            "'여름방학 (단축근무-계절학기)'. 어느 표에서 왔는지 사람이 구분하기 위한 값이므로 "
            "이미지에 적힌 그대로 옮길 것. 시간표가 아닌 안내도/지도 이미지면 null"
        )
    )
    trips: list[ParsedTrip]


class ShuttleTrip(ParsedTrip):
    """저장/응답용 출발 1건. ParsedTrip + 방향 상수에서 채운 노선 정보."""

    route: str
    origin: str
    via: list[str]
    destination: str


class ShuttleTimetable(BaseModel):
    """이미지 1장에서 추출한 시간표."""

    title: str | None = None
    trips: list[ShuttleTrip]
