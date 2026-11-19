"""관리자 화면 롤 분리: shuttle_admin은 셔틀 메뉴만, global_admin은 전체."""

import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.admin import mount_admin, shuttle_view
from app.config import Config
from app.utils import keycloak_auth
from tests.test_admin_auth import BASE, _FakeJwks, _login

ROOT = "/static-info/admin"
OTHERS = [
    "/restaurant/list",
    "/restaurant-image/list",
    "/organization",
    "/admin-audit-log/list",
]


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        Config, "SHUTTLE_TIMETABLE_PATH", str(tmp_path / "shuttle.json")
    )
    monkeypatch.setattr(Config, "ADMIN_PANEL_ENABLED", True)
    monkeypatch.setattr(Config, "KC_CLIENT_ID", "sandol-static-info")
    monkeypatch.setattr(Config, "KC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(Config, "STATIC_INFO_PUBLIC_BASE_URL", BASE)
    monkeypatch.setattr(Config, "ADMIN_SESSION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(keycloak_auth, "_jwks_client", lambda: _FakeJwks())

    def make(roles: list[str]) -> TestClient:
        app = FastAPI(root_path=Config.ROOT_PATH)
        assert mount_admin(app) is not None
        client = TestClient(app, base_url="https://testserver", follow_redirects=False)
        assert _login(client, monkeypatch, roles).status_code == 302
        return client

    return make


def _sync_post(client: TestClient, **extra: str):
    html = client.get(f"{ROOT}/shuttle").text
    token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert token
    return client.post(
        f"{ROOT}/shuttle",
        data={"csrf_token": token.group(1), "action": "sync", **extra},
    )


def test_shuttle_admin_only_shuttle(make_client, monkeypatch) -> None:
    client = make_client(["shuttle_admin"])
    assert client.get(f"{ROOT}/shuttle").status_code == 200
    for path in OTHERS:
        assert client.get(ROOT + path).status_code == 403, path
    assert client.post(f"{ROOT}/organization/save", data={}).status_code == 403
    menu = client.get(f"{ROOT}/shuttle").text
    assert "셔틀 시간표" in menu and "제휴 식당" not in menu


def test_shuttle_admin_force_sync_denied(make_client, monkeypatch, caplog) -> None:
    from unittest.mock import AsyncMock

    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(shuttle_view, "sync_shuttle_timetables", sync)
    client = make_client(["shuttle_admin"])
    res = _sync_post(client, force="1")
    assert res.status_code == 303
    sync.assert_not_called()
    assert "shuttle.timetable.refresh" not in caplog.text
    assert _sync_post(client).status_code == 303
    sync.assert_called_once_with(False)


def test_global_admin_all_allowed(make_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    client = make_client(["global_admin"])
    assert client.get(f"{ROOT}/shuttle").status_code == 200
    assert 'name="force"' in client.get(f"{ROOT}/shuttle").text
    for path in OTHERS:
        assert client.get(ROOT + path).status_code == 200, path
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(shuttle_view, "sync_shuttle_timetables", sync)
    _sync_post(client, force="1")
    sync.assert_called_once_with(True)


def test_legacy_session_without_full_admin_is_shuttle_only(make_client) -> None:
    client = make_client(["global_admin"])
    raw = client.cookies.get(admin_auth.SESSION_COOKIE)
    data = admin_auth._decrypt(raw)
    assert data is not None and data["full_admin"] is True
    del data["full_admin"]
    client.cookies.set(
        admin_auth.SESSION_COOKIE, admin_auth._encrypt(data), path="/static-info"
    )
    assert client.get(f"{ROOT}/shuttle").status_code == 200
    for path in OTHERS:
        assert client.get(ROOT + path).status_code == 403, path
