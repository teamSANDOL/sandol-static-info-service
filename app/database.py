"""TUK World용 동기 SQLite 엔진과 세션 팩토리."""

import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.schema import CreateColumn
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
    """저장 디렉터리와 테이블이 없으면 생성합니다."""
    # 모델을 import해야 Base.metadata에 테이블이 등록된다.
    from app.models import tuk_world  # noqa: F401

    os.makedirs(Config.TUK_WORLD_IMAGE_DIR, exist_ok=True)
    # ponytail: create_all + 누락 nullable 컬럼 ADD COLUMN만. 타입 변경·삭제가
    # 필요해지면 alembic 도입.
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """기존 테이블에 모델에는 있고 DB에는 없는 nullable 컬럼을 추가합니다(멱등)."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"NOT NULL 컬럼은 자동 추가 불가: {column}")
                ddl = CreateColumn(column).compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                if column.index:
                    conn.execute(
                        text(
                            f"CREATE INDEX IF NOT EXISTS ix_{table.name}_{column.name}"
                            f" ON {table.name} ({column.name})"
                        )
                    )
