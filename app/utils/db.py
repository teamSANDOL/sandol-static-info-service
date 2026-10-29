"""요청 단위 DB 세션 의존성."""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database import SessionLocal


def get_db() -> Iterator[Session]:
    """요청 단위 DB 세션을 제공합니다.

    Yields:
        요청이 끝나면 닫히는 SQLAlchemy 세션.
    """
    with SessionLocal() as session:
        yield session
