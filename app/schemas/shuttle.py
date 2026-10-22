"""셔틀버스 시간표 스키마. PDF 파싱 결과와 API 요청/응답에 공용으로 사용합니다."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"  # 24시간 HH:MM
TripKind = Literal["fixed", "frequent", "arrival_board"]
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
    """출발 1건. 노선/출발지/도착지는 방향 상수에서 채워지므로 여기 없음."""

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
        pattern=HHMM, description="출발 시각 HH:MM (24시간). 시간 범위면 시작 시각"
    )
    departure_time_end: str | None = Field(
        pattern=HHMM,
        description="수시운행 등 시간 범위일 때 종료 시각 HH:MM. 단일 시각이면 null",
    )
    valid_from: str | None = Field(
        description=(
            "이 출발 건이 속한 표의 운행 시작일 MM-DD. 제목 옆/표 위·우측의 기간 표기"
            "(예: '★★ 8.25 ~ 8.31 ★★', '7.14 ~ 8.24')에서 채움. 통합표(행마다 계절학기/단축근무/"
            "정상근무 라벨로 기간이 다름)면 그 행 라벨에 해당하는 기간을 씀. "
            "원본에 연도 표기가 없으므로 연도는 절대 쓰지 말 것(MM-DD만). "
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
    kind: TripKind = Field(
        default="fixed",
        description=(
            "구간 종류. fixed=정시 출발 1건, frequent=수시운행 구간(시작~종료), "
            "arrival_board=정시 출발 대신 도착하는 버스에 탑승하는 구간(종료=막차). "
            "생략하면 종료 시각이 있으면 frequent, 없으면 fixed"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _default_kind(cls, data: object) -> object:
        """kind가 없던 기존 입력(저장본·PUT 본문)도 그대로 받도록 기본값을 채웁니다."""
        if isinstance(data, dict) and not data.get("kind"):
            end = data.get("departure_time_end")
            return {**data, "kind": "frequent" if end else "fixed"}
        return data

    @model_validator(mode="after")
    def _range_needs_end(self) -> "ParsedTrip":
        """수시운행·도착버스 구간은 종료 시각(막차)이 있어야 앱이 구간으로 그립니다."""
        if self.kind != "fixed" and not self.departure_time_end:
            raise ValueError(f"kind={self.kind}에는 departure_time_end가 필요합니다.")
        return self


class ParsedTimetable(BaseModel):
    """PUT /bus/timetable/{place} 요청 본문."""

    title: str | None = Field(
        description=(
            "문서 상단 제목 원문. 예: '여름방학 (정상근무) 셔틀버스 시간표', "
            "'여름방학 (단축근무-계절학기)'. 어느 표에서 왔는지 사람이 구분하기 위한 값이므로 "
            "원문 그대로 옮길 것. 시간표가 아니면 null"
        )
    )
    trips: list[ParsedTrip]
    notes: list[str] | None = Field(
        default=None,
        description="표 밖 안내문(예: 소요시간). 생략하면 기존 문서의 안내문을 유지",
    )


class ShuttleTrip(ParsedTrip):
    """저장/응답용 출발 1건. ParsedTrip + 방향 상수에서 채운 노선 정보."""

    route: str
    origin: str
    via: list[str]
    destination: str


class ShuttleTimetable(BaseModel):
    """PDF 1건에서 추출한 시간표."""

    title: str | None = None
    trips: list[ShuttleTrip]


# ---- 관리자 시간표 API (/bus/admin/timetables) ----

# 공개 응답(GET /bus/timetable)의 directions[].id와 같은 계약 코드
DirectionId = Literal[
    "main_out",
    "main_in",
    "campus2_out",
    "campus2_in",
    "campus2_from_station",
    "campus2_to_station",
]
MMDD = r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$"


class AdminTripIn(BaseModel):
    """운행편 1건 입력. 시각은 24시간 HH:MM."""

    model_config = ConfigDict(extra="forbid")

    direction: DirectionId = Field(description="방향 계약 코드")
    day_type: DayType = Field(description="운행일. '주말'은 토·일 양쪽에 적용")
    kind: TripKind = Field(
        default="fixed",
        description="fixed=정시 1편, frequent=수시운행 구간, arrival_board=도착버스 탑승 구간",
    )
    time: str = Field(pattern=HHMM, description="출발 시각(구간이면 시작)")
    end: str | None = Field(
        default=None, pattern=HHMM, description="구간 종료(arrival_board는 막차)"
    )
    note: str | None = Field(default=None, description="예: 막차, 오이도역 도착")
    day_note: str | None = Field(default=None, description="예: 일학습병행학부")
    boarding_place: str | None = None
    valid_from: str | None = Field(
        default=None,
        pattern=MMDD,
        description="문서 기간 안에서 이 편만 다른 기간일 때 MM-DD. 보통 비움",
    )
    valid_to: str | None = Field(default=None, pattern=MMDD)
    period_note: str | None = None

    @model_validator(mode="after")
    def _check_range(self) -> "AdminTripIn":
        """정시 편엔 종료가 없고, 구간엔 시작 < 종료가 있어야 합니다."""
        if self.kind == "fixed" and self.end is not None:
            raise ValueError("fixed 편에는 end를 넣지 않습니다.")
        if self.kind != "fixed" and (self.end is None or not self.time < self.end):
            raise ValueError(f"{self.kind}에는 time보다 늦은 end가 필요합니다.")
        if (self.valid_from is None) != (self.valid_to is None):
            raise ValueError("valid_from과 valid_to는 함께 넣습니다.")
        return self


class AdminTripOut(AdminTripIn):
    """저장된 운행편. id는 문서 안에서 안정적이며 수정·삭제에 씁니다."""

    model_config = ConfigDict(extra="ignore")

    id: str


class AdminTimetableMeta(BaseModel):
    """문서 메타. 유효기간은 연도 포함 날짜이며 둘 다 비우면 상시."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    valid_from: date | None = None
    valid_to: date | None = None
    notes: list[str] = Field(default_factory=list, description="표 밖 안내문")

    @model_validator(mode="after")
    def _check_period(self) -> "AdminTimetableMeta":
        """유효기간은 둘 다 넣거나 둘 다 비우며, 시작 ≤ 종료여야 합니다."""
        if (self.valid_from is None) != (self.valid_to is None):
            raise ValueError("valid_from과 valid_to는 함께 넣습니다.")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from은 valid_to보다 늦을 수 없습니다.")
        return self


class AdminTimetableCreate(AdminTimetableMeta):
    """새 문서. place는 문서 키(중복 불가)."""

    place: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[^/\\\x00-\x1f\x7f]+$",
        description="문서 키. '/'·'\\'·줄바꿈 등 제어문자 불가, 100자 이내",
    )
    trips: list[AdminTripIn] = Field(default_factory=list)


class AdminTimetablePatch(BaseModel):
    """문서 메타 일부 수정. 보낸 필드만 바꿉니다(유효기간은 둘을 함께)."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    valid_from: date | None = None
    valid_to: date | None = None
    notes: list[str] | None = None


class AdminPending(BaseModel):
    """검토 대기 중인 새 PDF 파싱본 요약."""

    parsed_at: str | None
    pdf_url: str | None
    trip_count: int
    problems: list[str]


class AdminTimetableSummary(BaseModel):
    """문서 목록 1건. 유효기간은 연도 포함(기간 표기 없으면 null)."""

    place: str
    title: str | None
    source: Literal["pdf", "user"]
    valid_from: date | None
    valid_to: date | None
    trip_count: int
    needs_review: bool
    problems: list[str]
    pending: AdminPending | None
    updated_at: str | None
    updated_by: str | None
    auto_update: bool = Field(
        description=(
            "PDF 자동 갱신 대상인지. false면 관리자 수정본(source=user)이라 "
            "force 없는 동기화가 이 문서를 더는 갱신하지 않습니다."
        )
    )
    revision: str = Field(
        description="수정 버전. PATCH·운행편 PUT에 If-Match로 보내면 충돌 시 412"
    )


class AdminTimetableDetail(AdminTimetableSummary):
    """문서 상세. 운행편마다 안정 id가 있습니다."""

    pdf_url: str | None
    parsed_at: str | None
    notes: list[str]
    trips: list[AdminTripOut]
