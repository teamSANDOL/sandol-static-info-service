"""조직도 관리자 화면: 검증·원자적 저장·즉시 반영·쓰기 실패 처리."""

import json
import os
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import mount_admin
from app.config import Config
from app.routers import organization
from app.utils import keycloak_auth
from tests.test_admin_auth import BASE, _FakeJwks, _login

URL = "/static-info/admin/organization"
GOOD = {"본부": {"입학처": {"phone": "0311", "url": "https://x.test/"}}}


@pytest.fixture
def org(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "school_info.json"
    path.write_text(json.dumps({"기존": {"phone": "1"}}), encoding="utf-8")
    monkeypatch.setattr(Config, "SCHOOL_INFO_PATH", str(path))
    monkeypatch.setattr(Config, "ADMIN_PANEL_ENABLED", True)
    monkeypatch.setattr(Config, "KC_CLIENT_ID", "sandol-static-info")
    monkeypatch.setattr(Config, "KC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(Config, "STATIC_INFO_PUBLIC_BASE_URL", BASE)
    monkeypatch.setattr(Config, "ADMIN_SESSION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(keycloak_auth, "_jwks_client", lambda: _FakeJwks())
    app = FastAPI(root_path=Config.ROOT_PATH)
    app.include_router(organization.router)
    assert mount_admin(app) is not None
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    _login(client, monkeypatch, ["global_admin"])
    return client, path


def _csrf(client: TestClient) -> str:
    html = client.get(URL).text
    return re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)


def _post(client: TestClient, action: str, content: str, token: str | None = None):
    data = {"content": content, "csrf_token": token or _csrf(client)}
    return client.post(f"{URL}/{action}", data=data)


def test_page_shows_tree(org) -> None:
    client, _ = org
    res = client.get(URL)
    assert res.status_code == 200 and "기존" in res.text


def test_requires_login(org) -> None:
    client, _ = org
    client.cookies.clear()
    assert client.get(URL).status_code == 302


def test_csrf_required(org) -> None:
    client, path = org
    before = path.read_text(encoding="utf-8")
    res = client.post(f"{URL}/save", data={"content": json.dumps(GOOD)})
    assert res.status_code == 403
    assert _post(client, "save", json.dumps(GOOD), "bad").status_code == 403
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "bad", ["{not json", "[]", "{}", '{"a": "str"}', '{"a": {"phone": 1}}']
)
def test_invalid_rejected_and_file_kept(org, bad: str) -> None:
    client, path = org
    before = path.read_text(encoding="utf-8")
    res = _post(client, "save", bad)
    assert res.status_code == 400 and "alert-danger" in res.text
    assert path.read_text(encoding="utf-8") == before
    assert not os.path.exists(str(path) + ".bak")


def test_validate_does_not_write(org) -> None:
    client, path = org
    before = path.read_text(encoding="utf-8")
    res = _post(client, "validate", json.dumps(GOOD))
    assert res.status_code == 200 and "검증 통과" in res.text
    assert path.read_text(encoding="utf-8") == before


def test_save_applies_to_tree_with_backup(org) -> None:
    client, path = org
    res = _post(client, "save", json.dumps(GOOD))
    assert res.status_code == 200 and "저장했습니다" in res.text
    assert json.loads(path.read_text(encoding="utf-8")) == GOOD
    assert json.loads(Path(str(path) + ".bak").read_text(encoding="utf-8")) == {
        "기존": {"phone": "1"}
    }
    assert [p.name for p in path.parent.glob("*.tmp")] == []
    tree = client.get("/static-info/organization/tree").json()
    assert "입학처" in tree["subunits"]["본부"]["subunits"]
    assert "admin-sub" in client.get(URL).text  # updated_by 표시


def test_write_failure_shows_error_not_500(
    org, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, path = org
    before = path.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", boom)
    res = _post(client, "save", json.dumps(GOOD))
    assert res.status_code == 200 and "저장하지 못했습니다" in res.text
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in path.parent.glob("*.tmp")] == []
