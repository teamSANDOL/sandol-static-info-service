"""TUK World(총학생회 제휴 할인 식당) ORM 모델."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Restaurant(Base):
    """제휴 식당."""

    __tablename__ = "tuk_world_restaurant"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    naver_map_url: Mapped[str | None] = mapped_column(String(500))
    kakao_map_url: Mapped[str | None] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    discount_info: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
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
    restaurant: Mapped[Restaurant] = relationship(back_populates="images")
