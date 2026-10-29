"""Alembic 실행 환경. 앱과 같은 SQLite 엔진(app.database.engine)으로 마이그레이션한다."""

from logging.config import fileConfig

from alembic import context

# 모델을 import해야 Base.metadata에 테이블이 등록된다.
from app.database import Base, engine, init_db
from app.models import admin_audit, tuk_world  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """엔진에 연결해 마이그레이션을 실행한다.

    테스트처럼 호출자가 `config.attributes["connection"]`을 넘기면 그 연결을 쓴다.
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    init_db()  # SQLite 파일을 만들 디렉터리를 먼저 준비한다.
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
