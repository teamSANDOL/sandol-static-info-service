"""감사 로그 DB 누적, 실패 격리, 조회 전용 화면."""

import pytest
from sqlalchemy import select

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


def test_arecord_audit_runs_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    seen: list[bool] = []

    def spy(*args: object, **kwargs: object) -> None:
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)

    monkeypatch.setattr(audit, "record_audit", spy)
    asyncio.run(audit.arecord_audit("s", "a.b.c", "t"))
    assert seen == [False]  # 스레드에는 실행 중인 루프가 없다.
