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


def _client_allowed(payload: dict[str, Any]) -> bool:
    """토큰 azp 또는 aud가 KC_ALLOWED_CLIENTS에 있는지 확인합니다."""
    aud = payload.get("aud")
    claims = [payload.get("azp"), *(aud if isinstance(aud, list) else [aud])]
    return any(c in Config.KC_ALLOWED_CLIENTS for c in claims if isinstance(c, str))


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Bearer 토큰을 검증하고 관리자 롤(KC_ADMIN_ROLE)이 있으면 sub를 반환합니다.

    Keycloak 설정이 없으면 fail-closed로 모든 쓰기를 거부합니다.

    Raises:
        HTTPException: 미설정(503), 토큰 없음·무효(401), 롤 없음(403).
    """
    return _require_roles(credentials, [Config.KC_ADMIN_ROLE])[0]


def require_shuttle_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """셔틀 시간표 쓰기용. SHUTTLE_ADMIN_ROLES 중 하나가 있으면 sub를 반환합니다.

    Raises:
        HTTPException: 미설정(503), 토큰 없음·무효(401), 롤 없음(403).
    """
    return _require_roles(credentials, Config.SHUTTLE_ADMIN_ROLES)[0]


def shuttle_admin_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tuple[str, list[str]]:
    """require_shuttle_admin과 같되 (sub, realm 롤 목록)을 반환합니다.

    롤에 따라 일부 옵션(예: 수정본을 덮어쓰는 force 동기화)을 더 막을 때 씁니다.
    """
    return _require_roles(credentials, Config.SHUTTLE_ADMIN_ROLES)


def _require_roles(
    credentials: HTTPAuthorizationCredentials | None, allowed: list[str]
) -> tuple[str, list[str]]:
    """토큰을 검증하고 realm 롤이 allowed 중 하나와 겹치면 (sub, 롤 목록)을 반환합니다."""
    if not Config.KC_SERVER_URL:
        raise HTTPException(
            Config.HttpStatus.SERVICE_UNAVAILABLE,
            "관리자 인증이 설정되지 않았습니다.",
        )
    if credentials is None:
        raise HTTPException(
            Config.HttpStatus.UNAUTHORIZED,
            "Bearer 토큰이 필요합니다.",
            headers={"WWW-Authenticate": "Bearer"},
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
        raise HTTPException(
            Config.HttpStatus.SERVICE_UNAVAILABLE,
            "인증 서버 키를 조회할 수 없습니다.",
        ) from exc
    except jwt.PyJWTError as exc:
        logger.info("관리자 토큰 검증 실패: %s", type(exc).__name__)
        raise HTTPException(
            Config.HttpStatus.UNAUTHORIZED,
            "유효하지 않은 토큰입니다.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if Config.KC_ALLOWED_CLIENTS and not _client_allowed(payload):
        raise HTTPException(
            Config.HttpStatus.UNAUTHORIZED,
            "허용되지 않은 클라이언트의 토큰입니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    realm_access = payload.get("realm_access")
    roles = realm_access.get("roles") if isinstance(realm_access, dict) else None
    if not isinstance(roles, list) or not set(allowed) & set(roles):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "관리자 권한이 필요합니다.")
    return str(payload["sub"]), [str(r) for r in roles]
