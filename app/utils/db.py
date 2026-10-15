"""요청 단위 DB 세션 의존성."""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database import SessionLocal


def get_db() -> Iterator[Session]:
    """요청 단위 DB 세션을 제공합니다."""
    with SessionLocal() as session:
        yield session
