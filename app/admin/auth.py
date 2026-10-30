"""Keycloak OIDC 기반 sqladmin 관리자 인증.

/static-info/admin 은 Keycloak 로그인(Authorization Code + PKCE) 후
KC_ADMIN_ROLE 또는 shuttle_admin 등 Config.SHUTTLE_ADMIN_ROLES(realm role)를 가진
계정이 들어올 수 있고, 롤 판정은 쓰기 API와 같은 JWKS 서명 검증을 쓴다.
shuttle_admin은 "셔틀 시간표" 메뉴만 쓰고 나머지 메뉴는 KC_ADMIN_ROLE(full_admin)만 쓴다.
세션은 Fernet으로 암호화한 쿠키(sub, exp, full_admin)이며, 롤은 로그인할 때만 확인한다.
full_admin 키가 없는 기존 세션은 셔틀 전용으로 취급하므로 재로그인해야 한다.
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
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import Config, logger
from app.utils.audit import arecord_audit
from app.utils.keycloak_auth import expected_issuer, shuttle_admin_identity

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
    """세션 쿠키 암호화에 쓰는 Fernet 인스턴스를 만듭니다.

    Returns:
        ADMIN_SESSION_KEY로 만든 Fernet.
    """
    return Fernet(Config.ADMIN_SESSION_KEY)


def _encrypt(data: dict) -> str:
    """dict를 JSON으로 직렬화해 Fernet으로 암호화합니다.

    Args:
        data: 쿠키에 담을 값.

    Returns:
        쿠키 문자열.
    """
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def _decrypt(raw: str | None) -> dict | None:
    """쿠키를 복호화합니다. 없거나 위조·만료면 None.

    Args:
        raw: 쿠키 원문.

    Returns:
        복호화된 dict. 쿠키가 없거나 위조·만료됐으면 None.
    """
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
    """관리자 화면용 보안 쿠키를 응답에 설정합니다.

    Args:
        response: 쿠키를 붙일 응답.
        name: 쿠키 이름.
        value: 암호화된 쿠키 값.
        max_age: 유효 시간(초).
    """
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
        f"{expected_issuer()}/protocol/openid-connect/auth?{query}",
        status_code=Config.HttpStatus.FOUND,
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

    Args:
        code: Keycloak이 돌려준 authorization code.
        code_verifier: PKCE code_verifier.

    Returns:
        Keycloak 토큰 응답(JSON).

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


def is_full_admin(request: Request) -> bool:
    """세션이 KC_ADMIN_ROLE 보유자(전체 메뉴 허용)인지.

    full_admin 키가 없거나 bool True가 아니면 False(fail-closed).
    """
    data = _decrypt(request.cookies.get(SESSION_COOKIE))
    return data is not None and data.get("full_admin") is True


class FullAdminOnly:
    """셔틀 외 메뉴용 믹스인. 전체 관리자 세션이 아니면 메뉴를 숨기고 접근을 막는다."""

    def is_accessible(self, request: Request) -> bool:
        """sqladmin이 ModelView 라우트에서 호출하며, False면 403을 낸다."""
        return is_full_admin(request)

    def is_visible(self, request: Request) -> bool:
        """전체 관리자에게만 메뉴를 보여 줍니다."""
        return is_full_admin(request)


def require_full_admin(request: Request) -> None:
    """BaseView의 @expose 핸들러용 검사.

    sqladmin은 BaseView 라우트에서 is_accessible을 호출하지 않는다.

    Raises:
        HTTPException: 전체 관리자 세션이 아니면 403.
    """
    if not is_full_admin(request):
        raise HTTPException(Config.HttpStatus.FORBIDDEN, "full_admin_required")


def csrf_token(request: Request) -> str:
    """현재 세션에 묶인 CSRF 토큰. 커스텀 뷰 폼의 hidden 필드에 넣는다.

    Args:
        request: 현재 요청(세션 쿠키를 읽는다).

    Returns:
        세션 쿠키로부터 계산한 HMAC 16진 문자열.
    """
    raw = request.cookies.get(SESSION_COOKIE, "")
    return hmac.new(
        Config.ADMIN_SESSION_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def verify_csrf(request: Request, token: str | None) -> bool:
    """폼으로 받은 CSRF 토큰이 현재 세션 것인지 확인합니다.

    Args:
        request: 현재 요청(세션 쿠키를 읽는다).
        token: 폼으로 받은 토큰.

    Returns:
        세션이 유효하고 토큰이 일치하면 True.
    """
    if not token or admin_sub(request) is None:
        return False
    return hmac.compare_digest(token, csrf_token(request))


async def log_admin_action(request: Request, action: str, target: str) -> None:
    """감사 기록(로그 한 줄 + DB 행). 모든 관리자 뷰가 공유한다.

    Args:
        request: 현재 요청(수행자 sub를 세션에서 읽는다).
        action: `영역.대상.동작` 형식의 작업 이름.
        target: 대상 식별자.
    """
    await arecord_audit(admin_sub(request), action, target)


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
            f"{expected_issuer()}/protocol/openid-connect/logout",
            status_code=Config.HttpStatus.FOUND,
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
    """Keycloak 로그인 후 관리자 롤을 확인하고 세션 쿠키를 발급합니다.

    Args:
        request: 현재 요청(state 쿠키를 읽는다).
        code: Keycloak이 돌려준 authorization code.
        state: Keycloak이 돌려준 state.
        error: 로그인 실패 시 Keycloak이 보내는 오류 코드.

    Returns:
        관리자 화면으로 가는 리다이렉트(세션 쿠키 포함).

    Raises:
        HTTPException: 로그인 오류·state 불일치·토큰 교환 실패면 401.
            관리자·셔틀 관리자 롤이 모두 없으면 403.
    """
    if error:
        logger.warning("관리자 로그인 콜백 오류: %s", error)
        raise HTTPException(Config.HttpStatus.UNAUTHORIZED, "admin_login_failed")
    saved = _decrypt(request.cookies.get(STATE_COOKIE))
    if (
        not code
        or not state
        or saved is None
        or not hmac.compare_digest(str(saved.get("state", "")), state)
    ):
        raise HTTPException(Config.HttpStatus.UNAUTHORIZED, "invalid_oauth_state")

    try:
        token = await exchange_code(code, str(saved.get("code_verifier", "")))
        access_token = str(token["access_token"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("관리자 토큰 교환 실패: %s", type(exc).__name__)
        raise HTTPException(
            Config.HttpStatus.UNAUTHORIZED, "admin_token_exchange_failed"
        ) from exc

    # 쓰기 API와 같은 검증(JWKS 서명, iss, realm role)을 그대로 쓴다.
    # PyJWKClient의 동기 HTTP를 쓰므로 이벤트 루프 밖에서 실행한다.
    sub, roles = await run_in_threadpool(
        shuttle_admin_identity,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=access_token),
    )

    response = RedirectResponse(
        f"{Config.STATIC_INFO_PUBLIC_BASE_URL}/admin/",
        status_code=Config.HttpStatus.FOUND,
    )
    _set_cookie(
        response,
        SESSION_COOKIE,
        _encrypt(
            {
                "sub": sub,
                "exp": int(time.time()) + Config.ADMIN_SESSION_TTL_SECONDS,
                "full_admin": Config.KC_ADMIN_ROLE in roles,
            }
        ),
        Config.ADMIN_SESSION_TTL_SECONDS,
    )
    response.delete_cookie(STATE_COOKIE, path=Config.ROOT_PATH)
    await arecord_audit(sub, "admin.session.login", "-")
    return response
