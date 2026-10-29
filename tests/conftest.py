"""테스트가 저장소 data/ 대신 임시 디렉터리를 쓰도록 앱 import 전에 환경을 고정합니다."""

import os
import tempfile

os.environ["TUK_WORLD_DIR"] = tempfile.mkdtemp(prefix="tuk_world_test_")
os.environ["KC_SERVER_URL"] = "https://kc.test/auth/"

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

from app.database import init_db  # noqa: E402

# 앱 DB 스키마는 운영과 같이 alembic으로 만든다(create_all 경로 없음).
init_db()
command.upgrade(AlembicConfig("alembic.ini"), "head")
