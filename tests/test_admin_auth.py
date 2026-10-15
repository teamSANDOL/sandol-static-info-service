"""관리자 화면 인증: env 누락 시 미마운트(fail-closed), global_admin만 로그인."""

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from app.admin import auth as admin_auth
from app.admin import mount_admin
from app.config import Config
from app.utils import keycloak_auth

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
BASE = "https://example.test/static-info"


class _FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("K", (), {"key": KEY.public_key()})()


def _access_token(roles: list[str]) -> str:
    payload = {
        "sub": "admin-sub",
        "iss": keycloak_auth.expected_issuer(),
        "exp": int(time.time()) + 300,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(payload, KEY, algorithm="RS256")


@pytest.fixture
def admin_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(Config, "ADMIN_PANEL_ENABLED", True)
    monkeypatch.setattr(Config, "KC_CLIENT_ID", "sandol-static-info")
    monkeypatch.setattr(Config, "KC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(Config, "STATIC_INFO_PUBLIC_BASE_URL", BASE)
    monkeypatch.setattr(Config, "ADMIN_SESSION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(keycloak_auth, "_jwks_client", lambda: _FakeJwks())
    app = FastAPI(root_path=Config.ROOT_PATH)
    assert mount_admin(app) is not None
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def _login(client: TestClient, monkeypatch: pytest.MonkeyPatch, roles: list[str]):
    res = client.get("/static-info/admin/")
    assert res.status_code == 302
    state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]

    async def fake_exchange(code: str, code_verifier: str) -> dict:
        assert code == "the-code" and code_verifier
        return {"access_token": _access_token(roles)}

    monkeypatch.setattr(admin_auth, "exchange_code", fake_exchange)
    return client.get(
        "/static-info/admin-oauth/callback", params={"code": "the-code", "state": state}
    )


def test_not_mounted_when_env_missing() -> None:
    assert not admin_auth.admin_configured()
    client = TestClient(main.app, follow_redirects=False)
    assert client.get("/static-info/admin/").status_code == 404
    assert client.get("/static-info/admin-oauth/callback").status_code == 404


@pytest.mark.parametrize(
    "missing", ["KC_CLIENT_SECRET", "STATIC_INFO_PUBLIC_BASE_URL", "ADMIN_SESSION_KEY"]
)
def test_any_missing_env_disables_mount(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setattr(Config, missing, "")
    assert mount_admin(FastAPI()) is None


def test_invalid_fernet_key_disables_mount(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Config, "ADMIN_SESSION_KEY", "not-a-fernet-key")
    assert mount_admin(FastAPI()) is None


def test_anonymous_redirected_to_keycloak(admin_client: TestClient) -> None:
    res = admin_client.get("/static-info/admin/")
    assert res.status_code == 302
    loc = urlparse(res.headers["location"])
    q = parse_qs(loc.query)
    assert loc.path.endswith("/realms/Sandori/protocol/openid-connect/auth")
    assert q["client_id"] == ["sandol-static-info"]
    assert q["redirect_uri"] == [f"{BASE}/admin-oauth/callback"]
    assert q["code_challenge_method"] == ["S256"]


def test_global_admin_can_login(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = _login(admin_client, monkeypatch, ["global_admin"])
    assert res.status_code == 302 and res.headers["location"] == f"{BASE}/admin/"
    assert admin_client.get("/static-info/admin/").status_code == 200


def test_non_admin_rejected(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = _login(admin_client, monkeypatch, ["default-roles-sandori"])
    assert res.status_code == 403
    assert admin_auth.SESSION_COOKIE not in admin_client.cookies
    assert admin_client.get("/static-info/admin/").status_code == 302


def test_state_mismatch_rejected(admin_client: TestClient) -> None:
    admin_client.get("/static-info/admin/")
    res = admin_client.get(
        "/static-info/admin-oauth/callback", params={"code": "c", "state": "forged"}
    )
    assert res.status_code == 401


def test_forged_session_cookie_rejected(admin_client: TestClient) -> None:
    other = Fernet(Fernet.generate_key()).encrypt(b'{"sub": "x", "exp": 9999999999}')
    admin_client.cookies.set(
        admin_auth.SESSION_COOKIE, other.decode(), path="/static-info"
    )
    assert admin_client.get("/static-info/admin/").status_code == 302


def test_csrf_bound_to_session(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(admin_client, monkeypatch, ["global_admin"])
    raw = admin_client.cookies.get(admin_auth.SESSION_COOKIE)
    request = type("R", (), {"cookies": {admin_auth.SESSION_COOKIE: raw}})()
    token = admin_auth.csrf_token(request)
    assert admin_auth.verify_csrf(request, token)
    assert not admin_auth.verify_csrf(request, "0" * 64)
    assert admin_auth.admin_sub(request) == "admin-sub"
