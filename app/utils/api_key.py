"""X-API-Key 헤더 인증 의존성.

서버는 API Key 원본을 저장하지 않고 SHA-256 해시만 Config.STATIC_INFO_API_KEY_SHA256에 보관합니다.
"""

import hashlib
import secrets

from fastapi import Header, HTTPException

from app.config import Config, logger


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """X-API-Key 헤더를 해시로 검증합니다.

    Header를 필수(Header())로 선언하면 누락 시 FastAPI가 422를 반환하므로,
    "헤더가 없거나 일치하지 않으면 401"을 만족하려면 default=None으로 받아 직접 401을 던져야 합니다.
    해시가 설정되지 않았으면(fail-closed) 무조건 401 처리합니다.
    """
    expected = Config.STATIC_INFO_API_KEY_SHA256.strip().lower()
    if not expected or not x_api_key:
        logger.warning("X-API-Key 인증 실패")
        raise HTTPException(status_code=Config.HttpStatus.UNAUTHORIZED, detail="인증에 실패했습니다.")

    actual = hashlib.sha256(x_api_key.encode()).hexdigest().lower()
    if not secrets.compare_digest(actual, expected):
        logger.warning("X-API-Key 인증 실패")
        raise HTTPException(status_code=Config.HttpStatus.UNAUTHORIZED, detail="인증에 실패했습니다.")
