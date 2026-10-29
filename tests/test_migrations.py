"""Alembic 마이그레이션: 빈 DB, 기준 스키마만 있는 DB, 이미 컬럼이 다 있는 DB, 모델 일치."""

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text

from app.database import Base
from app.models import admin_audit, tuk_world  # noqa: F401

RESTAURANT = "tuk_world_restaurant"


def _upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = AlembicConfig("alembic.ini")
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def _cols(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'm.db'}")


def test_empty_db_matches_models(engine: Engine) -> None:
    _upgrade(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_baseline_only_db_gets_new_columns_and_keeps_rows(engine: Engine) -> None:
    _upgrade(engine, "0001")
    assert not {"category", "address", "updated_by"} & _cols(engine, RESTAURANT)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {RESTAURANT} (id, name, latitude, longitude, "
                "created_at, updated_at) VALUES (1, 'a', 1, 2, '2026-01-01', "
                "'2026-01-01')"
            )
        )
    _upgrade(engine)
    assert {"category", "address", "updated_by"} <= _cols(engine, RESTAURANT)
    assert "admin_audit_log" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT name, category FROM {RESTAURANT}")).one()
    assert tuple(row) == ("a", None)


def test_create_all_db_without_alembic_version_is_adopted(engine: Engine) -> None:
    """create_all(+ADD COLUMN)로 만든 옛 배포 DB: alembic_version 없이 컬럼이 다 있다."""
    Base.metadata.create_all(engine)
    _upgrade(engine)
    _upgrade(engine)  # 재실행도 안전
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert diff == []
    assert version == "0002"
