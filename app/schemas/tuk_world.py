"""TUK World(총학생회 제휴 할인 식당) 요청/응답 스키마."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl


def _as_utc(value: datetime) -> datetime:
    """SQLite가 tz를 잃은 naive 시각을 UTC로 간주해 tzinfo를 붙입니다."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]

Latitude = Field(ge=-90, le=90, description="위도 (WGS84)")
Longitude = Field(ge=-180, le=180, description="경도 (WGS84)")


class RestaurantCreate(BaseModel):
    """식당 등록 요청."""

    name: str = Field(min_length=1, max_length=100, description="식당명")
    naver_map_url: HttpUrl | None = Field(None, description="네이버 지도 링크")
    kakao_map_url: HttpUrl | None = Field(None, description="카카오맵 링크")
    latitude: float = Latitude
    longitude: float = Longitude
    discount_info: str | None = Field(
        None, description="할인 정보(비고). 형식 자유, 줄바꿈 허용"
    )


class RestaurantUpdate(BaseModel):
    """식당 부분 수정 요청. 보낸 필드만 바뀝니다. null을 보내면 값이 지워집니다."""

    name: str | None = Field(None, min_length=1, max_length=100)
    naver_map_url: HttpUrl | None = None
    kakao_map_url: HttpUrl | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    discount_info: str | None = None


class RestaurantImageOut(BaseModel):
    """식당 이미지 정보."""

    id: int
    url: str = Field(description="이미지 파일 조회 경로")
    content_type: str
    created_at: UtcDatetime


class RestaurantOut(BaseModel):
    """식당 응답."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    naver_map_url: str | None
    kakao_map_url: str | None
    latitude: float
    longitude: float
    discount_info: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    images: list[RestaurantImageOut]
