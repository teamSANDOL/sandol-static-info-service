"""TUK World(총학생회 제휴 할인 식당) ORM 모델."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    """현재 시각을 UTC로 반환합니다(컬럼 기본값·갱신값용).

    Returns:
        tz 정보가 붙은 현재 UTC 시각.
    """
    return datetime.now(UTC)


class Restaurant(Base):
    """제휴 식당."""

    __tablename__ = "tuk_world_restaurant"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # 분류(예: 식당·술집·여가). 자유 문자열
    category: Mapped[str | None] = mapped_column(String(20), index=True)
    address: Mapped[str | None] = mapped_column(String(200))
    naver_map_url: Mapped[str | None] = mapped_column(String(500))
    kakao_map_url: Mapped[str | None] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    discount_info: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    # 마지막으로 바꾼 관리자 Keycloak sub. 공개 응답에는 노출하지 않는다.
    updated_by: Mapped[str | None] = mapped_column(String(100))
    images: Mapped[list["RestaurantImage"]] = relationship(
        back_populates="restaurant",
        cascade="all, delete-orphan",
        order_by="RestaurantImage.id",
    )


class RestaurantImage(Base):
    """식당 이미지. 파일은 TUK_WORLD_IMAGE_DIR/filename 에 저장된다."""

    __tablename__ = "tuk_world_restaurant_image"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(
        ForeignKey("tuk_world_restaurant.id", ondelete="CASCADE"), index=True
    )
    # 서버가 생성한 이름(uuid hex + 확장자)만 저장한다. 사용자 파일명은 쓰지 않는다.
    filename: Mapped[str] = mapped_column(String(64), unique=True)
    content_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_by: Mapped[str | None] = mapped_column(String(100))
    restaurant: Mapped[Restaurant] = relationship(back_populates="images")
