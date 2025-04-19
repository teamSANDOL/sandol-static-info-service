"""FastAPI 앱의 설정을 정의하는 모듈입니다."""

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

# 핸들러 1: 파일에 모든 로그 저장 (디버깅용)
file_handler = logging.FileHandler(
    os.path.join(SERVICE_DIR, "app.log"), encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)  # DEBUG 이상 저장
file_formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
)
file_handler.setFormatter(file_formatter)

# 핸들러 2: 콘솔에 INFO 이상만 출력 (간결한 버전)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)  # INFO 이상만 출력
console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

# 로거에 핸들러 추가
logger.addHandler(file_handler)
logger.addHandler(console_handler)


class Config:
    """FastAPI 설정 값을 관리하는 클래스"""

    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    SHUTTLE_URL: str = "https://ibook.tukorea.ac.kr/Viewer/bus01"

    class HttpStatus:
        """HTTP 상태 코드를 정의하는 클래스"""

        OK = 200
        CREATED = 201
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