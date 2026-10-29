"""Keycloak OIDC 기반 sqladmin 관리자 인증.

/static-info/admin 은 Keycloak 로그인(Authorization Code + PKCE) 후
KC_ADMIN_ROLE(realm role)을 가진 계정만 들어올 수 있다. 롤 판정은 쓰기 API와 같은
`app.utils.keycloak_auth.require_admin`(JWKS 서명 검증)을 그대로 쓴다.
세션은 Fernet으로 암호화한 쿠키(sub, exp)이며, 롤은 로그인할 때만 확인한다.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import Config, logger
from app.utils.audit import record_audit
from app.utils.keycloak_auth import expected_issuer, require_admin

# kakao-bot 관리자 쿠키(sandol_admin_session)와 같은 도메인을 쓰므로 이름을 구분한다.
SESSION_COOKIE = "sandol_static_info_admin_session"
STATE_COOKIE = "sandol_static_info_admin_oauth_state"
_STATE_TTL_SECONDS = 600


def admin_configured() -> bool:
    """관리자 화면을 띄울 설정이 모두 있는지. 하나라도 없으면 마운트하지 않는다."""
    if not (
        Config.ADMIN_PANEL_ENABLED
        and Config.KC_SERVER_URL
        and Config.KC_CLIENT_ID
        and Config.KC_CLIENT_SECRET
        and Config.STATIC_INFO_PUBLIC_BASE_URL
        and Config.ADMIN_SESSION_KEY
    ):
        return False
    try:
        Fernet(Config.ADMIN_SESSION_KEY)
    except ValueError:
        logger.error("ADMIN_SESSION_KEY가 올바른 Fernet 키가 아닙니다.")
        return False
    return True


def _fernet() -> Fernet:
    return Fernet(Config.ADMIN_SESSION_KEY)


def _encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def _decrypt(raw: str | None) -> dict | None:
    """쿠키를 복호화합니다. 없거나 위조·만료면 None."""
    if not raw:
        return None
    try:
        data = json.loads(_fernet().decrypt(raw.encode()))
    except (InvalidToken, ValueError):
        return None
    if not isinstance(data, dict) or int(data.get("exp", 0)) <= int(time.time()):
        return None
    return data


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=not Config.DEBUG,
        samesite="lax",
        path=Config.ROOT_PATH,
    )


def redirect_uri() -> str:
    """Keycloak client에 등록할 콜백 주소."""
    return f"{Config.STATIC_INFO_PUBLIC_BASE_URL}/admin-oauth/callback"


def build_login_redirect() -> Response:
    """state·PKCE를 암호화 쿠키에 담고 Keycloak 로그인 페이지로 보냅니다."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    query = urlencode(
        {
            "client_id": Config.KC_CLIENT_ID,
            "response_type": "code",
            "scope": "openid",
            "redirect_uri": redirect_uri(),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(
        f"{expected_issuer()}/protocol/openid-connect/auth?{query}", status_code=302
    )
    cookie = _encrypt(
        {
            "state": state,
            "code_verifier": verifier,
            "exp": int(time.time()) + _STATE_TTL_SECONDS,
        }
    )
    _set_cookie(response, STATE_COOKIE, cookie, _STATE_TTL_SECONDS)
    return response


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Authorization Code를 토큰으로 교환합니다.

    Raises:
        httpx.HTTPError: 네트워크 오류 또는 Keycloak이 4xx/5xx를 반환한 경우.
    """
    token_url = (
        Config.KC_TOKEN_URL or f"{expected_issuer()}/protocol/openid-connect/token"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(),
                "code_verifier": code_verifier,
                "client_id": Config.KC_CLIENT_ID,
                "client_secret": Config.KC_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()


def admin_sub(request: Request) -> str | None:
    """유효한 관리자 세션의 Keycloak sub. 세션이 없으면 None. updated_by 기록에 쓴다."""
    data = _decrypt(request.cookies.get(SESSION_COOKIE))
    sub = data.get("sub") if data else None
    return sub if isinstance(sub, str) and sub else None


def csrf_token(request: Request) -> str:
    """현재 세션에 묶인 CSRF 토큰. 커스텀 뷰 폼의 hidden 필드에 넣는다."""
    raw = request.cookies.get(SESSION_COOKIE, "")
    return hmac.new(
        Config.ADMIN_SESSION_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def verify_csrf(request: Request, token: str | None) -> bool:
    """폼으로 받은 CSRF 토큰이 현재 세션 것인지 확인합니다."""
    if not token or admin_sub(request) is None:
        return False
    return hmac.compare_digest(token, csrf_token(request))


def log_admin_action(request: Request, action: str, target: str) -> None:
    """감사 기록(로그 한 줄 + DB 행). 모든 관리자 뷰가 공유한다."""
    record_audit(admin_sub(request), action, target)


class KeycloakAdminAuth(AuthenticationBackend):
    """Keycloak 로그인과 관리자 롤을 요구하는 sqladmin 인증 백엔드."""

    def __init__(self) -> None:
        """SessionMiddleware 대신 Fernet 암호화 쿠키를 직접 사용합니다."""
        self.middlewares = []

    async def login(self, request: Request) -> bool:
        """ID/PW 폼 로그인은 쓰지 않습니다. Keycloak 로그인만 허용합니다."""
        return False

    async def logout(self, request: Request) -> Response | bool:
        """세션 쿠키를 지우고 Keycloak 로그아웃으로 보냅니다."""
        response = RedirectResponse(
            f"{expected_issuer()}/protocol/openid-connect/logout", status_code=302
        )
        response.delete_cookie(SESSION_COOKIE, path=Config.ROOT_PATH)
        return response

    async def authenticate(self, request: Request) -> Response | bool:
        """유효한 세션이 없으면 Keycloak 로그인으로 리다이렉트합니다."""
        if admin_sub(request):
            return True
        return build_login_redirect()


router = APIRouter(include_in_schema=False)


@router.get("/admin-oauth/callback")
async def admin_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Keycloak 로그인 후 관리자 롤을 확인하고 세션 쿠키를 발급합니다."""
    if error:
        logger.warning("관리자 로그인 콜백 오류: %s", error)
        raise HTTPException(401, "admin_login_failed")
    saved = _decrypt(request.cookies.get(STATE_COOKIE))
    if (
        not code
        or not state
        or saved is None
        or not hmac.compare_digest(str(saved.get("state", "")), state)
    ):
        raise HTTPException(401, "invalid_oauth_state")

    try:
        token = await exchange_code(code, str(saved.get("code_verifier", "")))
        access_token = str(token["access_token"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("관리자 토큰 교환 실패: %s", type(exc).__name__)
        raise HTTPException(401, "admin_token_exchange_failed") from exc

    # 쓰기 API와 같은 검증(JWKS 서명, iss, realm role)을 그대로 쓴다.
    sub = require_admin(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token)
    )

    response = RedirectResponse(
        f"{Config.STATIC_INFO_PUBLIC_BASE_URL}/admin/", status_code=302
    )
    _set_cookie(
        response,
        SESSION_COOKIE,
        _encrypt(
            {"sub": sub, "exp": int(time.time()) + Config.ADMIN_SESSION_TTL_SECONDS}
        ),
        Config.ADMIN_SESSION_TTL_SECONDS,
    )
    response.delete_cookie(STATE_COOKIE, path=Config.ROOT_PATH)
    record_audit(sub, "login", "-")
    return response
