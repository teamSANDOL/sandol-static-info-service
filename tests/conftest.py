"""테스트가 저장소 data/ 대신 임시 디렉터리를 쓰도록 앱 import 전에 환경을 고정합니다."""

import os
import tempfile

os.environ["TUK_WORLD_DIR"] = tempfile.mkdtemp(prefix="tuk_world_test_")
os.environ["KC_SERVER_URL"] = "https://kc.test/auth/"
