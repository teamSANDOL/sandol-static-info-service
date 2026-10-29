"""TUK World용 동기 SQLite 엔진과 세션 팩토리."""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import Config


class Base(DeclarativeBase):
    """TUK World 테이블의 선언 베이스."""


# SQLite는 첫 connect 시점에 파일을 만들므로 import 때 디렉터리가 없어도 안전하다.
engine = create_engine(
    f"sqlite:///{Config.TUK_WORLD_DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    """이미지 저장 디렉터리(와 그 상위 DB 디렉터리)를 만듭니다.

    스키마는 만들지 않습니다. `alembic upgrade head`가 담당합니다.
    """
    os.makedirs(Config.TUK_WORLD_IMAGE_DIR, exist_ok=True)
