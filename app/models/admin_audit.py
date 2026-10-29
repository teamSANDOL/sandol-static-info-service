"""관리자 감사 로그 ORM 모델. 추가 전용이며 삭제 로직은 두지 않는다."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class AdminAuditLog(Base):
    """관리자 쓰기 작업 한 건."""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    actor_sub: Mapped[str | None] = mapped_column(String(100), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target: Mapped[str] = mapped_column(String(500))
    # 대상 식별자와 짧은 요약만(JSON 텍스트). 변경 전후 전체 내용은 저장하지 않는다.
    detail: Mapped[str | None] = mapped_column(Text)
