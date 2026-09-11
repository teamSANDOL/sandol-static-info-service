"""FastAPI 앱의 설정을 정의하는 모듈입니다."""

import json
import os
import logging
from dotenv import load_dotenv

# 환경 변수 로딩
load_dotenv()

# 현재 파일이 위치한 디렉터리 (config 폴더의 절대 경로)
CONFIG_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.abspath(CONFIG_DIR)

SERVICE_DIR = os.path.abspath(os.path.join(CONFIG_DIR, "../.."))
# 로깅 설정
logger = logging.getLogger("sandol_static_info_service")
logger.setLevel(logging.DEBUG)  # 모든 로그 기록

console_handler = logging.StreamHandler()
if os.getenv("DEBUG", "False").lower() == "true":
    console_handler.setLevel(logging.DEBUG)  # DEBUG 이상 출력
else:
    # DEBUG 모드가 아닐 때는 INFO 이상만 출력
    console_handler.setLevel(logging.INFO)  # INFO 이상만 출력
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)


logger.addHandler(console_handler)


class Config:
    """FastAPI 설정 값을 관리하는 클래스"""

    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    # API Key 원본은 저장하지 않고 SHA-256 hex 해시만 보관 (X-API-Key 인증용)
    STATIC_INFO_API_KEY_SHA256: str = os.getenv("STATIC_INFO_API_KEY_SHA256", "")
    SHUTTLE_URL: str = "https://ibook.tukorea.ac.kr/Viewer/bus01"
    # 방학 중엔 페이지 수/구성이 유동적(안내도, 기간별 개별표, 통합표 등)이라
    # 페이지 → 노선 고정 매핑은 두지 않음. place는 항상 "{idx}페이지"(출처 페이지 번호)로 저장

    # 셔틀 시간표 AI 파싱 (OpenAI)
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    OPENAI_REASONING_EFFORT: str = os.getenv("OPENAI_REASONING_EFFORT", "high")
    # flex: 저렴/느림, 혼잡 시 429. 기본 tier로 되돌리려면 "default"
    OPENAI_SERVICE_TIER: str = os.getenv("OPENAI_SERVICE_TIER", "flex")
    OPENAI_TIMEOUT: float = float(os.getenv("OPENAI_TIMEOUT", "900"))

    # 파싱 결과 저장 위치 (compose에서 bind mount)
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(SERVICE_DIR, "data"))
    SHUTTLE_TIMETABLE_PATH: str = os.path.join(DATA_DIR, "shuttle_timetable.json")
    SHUTTLE_IMAGE_DIR: str = os.path.join(DATA_DIR, "shuttle_images")
    school_info_path: str = os.path.join(
        os.path.abspath(os.path.join(CONFIG_DIR, "school_info.json"))
    )

    class HttpStatus:
        """HTTP 상태 코드를 정의하는 클래스"""

        OK = 200
        CREATED = 201
        ACCEPTED = 202
        NO_CONTENT = 204
        BAD_REQUEST = 400
        UNAUTHORIZED = 401
        FORBIDDEN = 403
        NOT_FOUND = 404
        NOT_ACCEPTABLE = 406
        CONFLICT = 409
        UNSUPPORTED_MEDIA_TYPE = 415
        INTERNAL_SERVER_ERROR = 500
        NOT_IMPLEMENTED = 501
        BAD_GATEWAY = 502

    class Accept:
        """Accept 헤더를 정의하는 클래스"""

        JSON = "application/json"
        BASE64 = "application/base64"
        ZIP = "application/zip"
        OCTET_STREAM = "application/octet-stream"

    class ImageType:
        """이미지 타입을 정의하는 클래스"""

        JPEG = "image/jpeg"
        PNG = "image/png"
        GIF = "image/gif"

    @staticmethod
    def get_school_info_file():
        with open(Config.school_info_path, "r", encoding="utf-8") as f:
            return json.load(f)
