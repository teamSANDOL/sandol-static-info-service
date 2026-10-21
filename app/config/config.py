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
    SHUTTLE_URL: str = "https://ibook.tukorea.ac.kr/Viewer/bus01"
    WEEKLY_MENU_URL: str = "https://ibook.tukorea.ac.kr/Viewer/menu02"

    # 셔틀 시간표는 iBook 원본 PDF(/web/RawFileList)를 좌표 기반으로 파싱합니다.
    # 외부 AI 호출은 쓰지 않습니다.

    # 파싱 결과 저장 위치 (compose에서 bind mount)
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(SERVICE_DIR, "data"))
    SHUTTLE_TIMETABLE_PATH: str = os.path.join(DATA_DIR, "shuttle_timetable.json")
    SHUTTLE_PDF_DIR: str = os.path.join(DATA_DIR, "shuttle_pdf")
    # 저장소에 들어 있는 정적 자료(배포 이미지에 포함). 날짜·정류장은 이 파일을 고칩니다.
    HOLIDAYS_PATH: str = os.path.join(CONFIG_DIR, "holidays.json")
    SHUTTLE_STOPS_PATH: str = os.path.join(CONFIG_DIR, "shuttle_stops.json")
    # TUK World(총학생회 제휴 식당): SQLite + 이미지 디렉터리 (compose에서 볼륨 마운트)
    TUK_WORLD_DIR: str = os.getenv("TUK_WORLD_DIR", os.path.join(DATA_DIR, "tuk_world"))
    TUK_WORLD_DB_PATH: str = os.path.join(TUK_WORLD_DIR, "tuk_world.db")
    TUK_WORLD_IMAGE_DIR: str = os.path.join(TUK_WORLD_DIR, "images")
    TUK_WORLD_MAX_IMAGE_BYTES: int = int(
        os.getenv("TUK_WORLD_MAX_IMAGE_BYTES", str(5 * 1024 * 1024))
    )

    # 쓰기 API 보호용 Keycloak 설정. KC_SERVER_URL이 비어 있으면 쓰기는 모두 거부된다.
    # KC_SERVER_URL은 토큰 iss와 같은 공개 주소, KC_JWKS_URL은 내부망 주소로 바꿀 수 있다.
    KC_SERVER_URL: str = (
        os.getenv("KC_SERVER_URL", "").rstrip("/") + "/"
        if os.getenv("KC_SERVER_URL")
        else ""
    )
    KC_REALM: str = os.getenv("KC_REALM", "Sandori")
    KC_ADMIN_ROLE: str = os.getenv("KC_ADMIN_ROLE", "global_admin")
    KC_JWKS_URL: str = os.getenv("KC_JWKS_URL", "")
    # 쉼표 구분 허용 클라이언트. 비어 있으면 azp/aud를 검사하지 않는다.
    KC_ALLOWED_CLIENTS: list[str] = [
        c.strip() for c in os.getenv("KC_ALLOWED_CLIENTS", "").split(",") if c.strip()
    ]
    # 이미지 URL 절대화용 공개 주소(예: https://sandori.kr). 비어 있으면 상대경로.
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    # 관리자 화면(sqladmin, /static-info/admin). 아래 값이 하나라도 비면 마운트하지 않는다.
    ROOT_PATH: str = "/static-info"
    ADMIN_PANEL_ENABLED: bool = (
        os.getenv("ADMIN_PANEL_ENABLED", "False").lower() == "true"
    )
    # static-info 전용 confidential client. redirect URI는
    # {STATIC_INFO_PUBLIC_BASE_URL}/admin-oauth/callback 으로 Keycloak에 등록한다.
    KC_CLIENT_ID: str = os.getenv("KC_CLIENT_ID", "")
    KC_CLIENT_SECRET: str = os.getenv("KC_CLIENT_SECRET", "")
    # 컨테이너에서 공개 주소로 토큰 교환이 안 될 때 내부 주소로 지정 (KC_JWKS_URL과 같은 용도)
    KC_TOKEN_URL: str = os.getenv("KC_TOKEN_URL", "")
    STATIC_INFO_PUBLIC_BASE_URL: str = os.getenv(
        "STATIC_INFO_PUBLIC_BASE_URL", ""
    ).rstrip("/")
    # 세션·state 쿠키 암호화용 Fernet 키
    ADMIN_SESSION_KEY: str = os.getenv("ADMIN_SESSION_KEY", "")
    ADMIN_SESSION_TTL_SECONDS: int = int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "3600"))

    # 조직도 원본. 관리자 편집(원자적 교체)을 위해 운영에선 디렉터리째 rw 마운트한다.
    SCHOOL_INFO_PATH: str = os.getenv(
        "SCHOOL_INFO_PATH", os.path.join(CONFIG_DIR, "school_info.json")
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
        REQUEST_ENTITY_TOO_LARGE = 413
        UNSUPPORTED_MEDIA_TYPE = 415
        UNPROCESSABLE_ENTITY = 422
        INTERNAL_SERVER_ERROR = 500
        NOT_IMPLEMENTED = 501
        BAD_GATEWAY = 502
        SERVICE_UNAVAILABLE = 503

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
        """조직도 원본(SCHOOL_INFO_PATH)을 읽어 dict로 반환합니다."""
        with open(Config.SCHOOL_INFO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
