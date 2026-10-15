"""TUK World(총학생회 제휴 할인 식당) ORM 모델과 SQLite 세션."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.config import Config


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """TUK World 테이블의 선언 베이스."""


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


os.makedirs(Config.TUK_WORLD_IMAGE_DIR, exist_ok=True)
engine = create_engine(
    f"sqlite:///{Config.TUK_WORLD_DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    """테이블이 없으면 생성합니다."""
    # ponytail: create_all만 사용. 컬럼 변경이 생기면 alembic 도입.
    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    """요청 단위 DB 세션을 제공합니다."""
    with SessionLocal() as session:
        yield session
