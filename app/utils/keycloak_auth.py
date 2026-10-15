"""Keycloak Bearer 토큰(JWKS 서명 검증) 기반 관리자 쓰기 권한 검사."""

from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Config, logger

_bearer = HTTPBearer(auto_error=False)


def expected_issuer() -> str:
    """토큰 iss로 기대하는 Keycloak realm 주소."""
    return f"{Config.KC_SERVER_URL}realms/{Config.KC_REALM}"


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    url = Config.KC_JWKS_URL or f"{expected_issuer()}/protocol/openid-connect/certs"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Bearer 토큰을 검증하고 관리자 롤이 있으면 sub를 반환합니다.

    Keycloak 설정이 없으면 fail-closed로 모든 쓰기를 거부합니다.

    Raises:
        HTTPException: 미설정(503), 토큰 없음·무효(401), 롤 없음(403).
    """
    if not Config.KC_SERVER_URL:
        raise HTTPException(503, "admin_auth_not_configured")
    if credentials is None:
        raise HTTPException(
            401, "missing_bearer_token", headers={"WWW-Authenticate": "Bearer"}
        )
    token = credentials.credentials
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        payload: dict[str, Any] = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=expected_issuer(),
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        logger.error("Keycloak JWKS 조회 실패: %s", exc)
        raise HTTPException(503, "jwks_unavailable") from exc
    except jwt.PyJWTError as exc:
        logger.info("관리자 토큰 검증 실패: %s", type(exc).__name__)
        raise HTTPException(
            401, "invalid_token", headers={"WWW-Authenticate": "Bearer"}
        ) from exc

    realm_access = payload.get("realm_access")
    roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    if not isinstance(roles, list) or Config.KC_ADMIN_ROLE not in roles:
        raise HTTPException(403, "missing_admin_role")
    return str(payload["sub"])
