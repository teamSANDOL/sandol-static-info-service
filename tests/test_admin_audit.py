"""감사 로그 DB 누적, 실패 격리, 조회 전용 화면, updated_by, 컬럼 추가."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app import database
from app.admin.audit_view import AuditLogAdmin
from app.database import SessionLocal, init_db
from app.models.admin_audit import AdminAuditLog
from app.utils import audit
from app.utils.audit import record_audit


@pytest.fixture(autouse=True)
def _db() -> None:
    init_db()


def _count() -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(AdminAuditLog)).all())


def test_rows_accumulate() -> None:
    before = _count()
    record_audit("s1", "a", "t1", detail={"id": 1})
    record_audit("s1", "a", "t2")
    assert _count() == before + 2
    with SessionLocal() as db:
        row = db.scalars(select(AdminAuditLog).order_by(AdminAuditLog.id)).all()[-2]
    assert (row.actor_sub, row.action, row.detail) == ("s1", "a", '{"id": 1}')


def test_db_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(audit, "SessionLocal", boom)
    record_audit("s1", "a", "t")  # 예외 없이 반환


def test_audit_view_is_read_only() -> None:
    assert not AuditLogAdmin.can_create
    assert not AuditLogAdmin.can_edit
    assert not AuditLogAdmin.can_delete


def test_updated_by_column_added_to_existing_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    database.Base.metadata.create_all(old)
    with old.begin() as conn:
        conn.execute(text("ALTER TABLE tuk_world_restaurant DROP COLUMN updated_by"))
    monkeypatch.setattr(database, "engine", old)
    database._add_missing_columns()
    cols = {c["name"] for c in inspect(old).get_columns("tuk_world_restaurant")}
    assert "updated_by" in cols
