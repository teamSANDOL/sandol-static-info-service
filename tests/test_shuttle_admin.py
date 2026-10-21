"""셔틀 시간표: 쓰기 API 관리자 Bearer 보호와 관리자 화면 등록·편집·삭제·동기화."""

import re
import time
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import auth as admin_auth
from app.admin import mount_admin
from app.admin import shuttle_view
from app.config import Config
from app.routers import bus
from app.utils import keycloak_auth
from app.utils import shuttle_timetable as st
from main import app

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TRIP = {
    "direction": "정왕역 → 본교",
    "day_type": "평일",
    "day_note": None,
    "departure_time": "08:40",
    "departure_time_end": None,
    "valid_from": "09-01",
    "valid_to": "12-22",
    "period_note": None,
    "boarding_place": None,
    "note": "첫차",
}


class _FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("K", (), {"key": KEY.public_key()})()


def _token(roles: list[str]) -> str:
    payload = {
        "sub": "admin-sub",
        "iss": keycloak_auth.expected_issuer(),
        "exp": int(time.time()) + 300,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(payload, KEY, algorithm="RS256")


def _bearer(roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(roles)}"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        Config, "SHUTTLE_TIMETABLE_PATH", str(tmp_path / "shuttle_timetable.json")
    )
    monkeypatch.setattr(keycloak_auth, "_jwks_client", lambda: _FakeJwks())


# ---- REST 쓰기 API ----

api = TestClient(app)
PLACE = "직접 등록 시간표"


def test_write_api_requires_bearer() -> None:
    body = {"title": "t", "trips": [TRIP]}
    assert api.put(f"/bus/timetable/{PLACE}", json=body).status_code == 401
    assert api.delete(f"/bus/timetable/{PLACE}").status_code == 401
    assert api.post("/bus/timetable/refresh").status_code == 401


def test_write_api_requires_admin_role() -> None:
    headers = _bearer(["default-roles-sandori"])
    body = {"title": "t", "trips": [TRIP]}
    assert (
        api.put(f"/bus/timetable/{PLACE}", json=body, headers=headers).status_code
        == 403
    )
    assert api.delete(f"/bus/timetable/{PLACE}", headers=headers).status_code == 403
    assert api.post("/bus/timetable/refresh", headers=headers).status_code == 403


def test_write_api_fail_closed_without_keycloak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Config, "KC_SERVER_URL", "")
    res = api.put(
        f"/bus/timetable/{PLACE}",
        json={"title": "t", "trips": [TRIP]},
        headers=_bearer(["global_admin"]),
    )
    assert res.status_code == 503
    assert st.load_store() == {}


def test_admin_put_keeps_title_and_updated_by(
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = _bearer(["global_admin"])
    res = api.put(
        f"/bus/timetable/{PLACE}",
        json={"title": "2학기 직접 등록", "trips": [TRIP]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    doc = st.load_store()[PLACE]
    assert doc["title"] == "2학기 직접 등록"
    assert doc["source"] == "user" and doc["updated_by"] == "admin-sub"
    assert "admin_audit sub=admin-sub action=shuttle_put" in caplog.text

    # GET은 공개 유지
    got = api.get("/bus/timetable", params={"date": "2026-10-22"})
    assert got.status_code == 200
    main_in = got.json()["routes"][0]["directions"][1]
    assert main_in["segments"] == [{"type": "fixed", "time": "08:40", "note": "첫차"}]

    assert api.delete(f"/bus/timetable/{PLACE}", headers=headers).status_code == 204
    assert api.delete(f"/bus/timetable/{PLACE}", headers=headers).status_code == 404


def test_admin_refresh_queues_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(bus, "sync_shuttle_timetables", sync)
    res = api.post(
        "/bus/timetable/refresh",
        params={"force": "true"},
        headers=_bearer(["global_admin"]),
    )
    assert res.status_code == 202
    sync.assert_called_once_with(True)


# ---- 관리자 화면 ----


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(Config, "ADMIN_PANEL_ENABLED", True)
    monkeypatch.setattr(Config, "KC_CLIENT_ID", "sandol-static-info")
    monkeypatch.setattr(Config, "KC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(
        Config, "STATIC_INFO_PUBLIC_BASE_URL", "https://example.test/static-info"
    )
    monkeypatch.setattr(Config, "ADMIN_SESSION_KEY", Fernet.generate_key().decode())
    admin_app = FastAPI(root_path=Config.ROOT_PATH)
    assert mount_admin(admin_app) is not None
    client = TestClient(
        admin_app, base_url="https://testserver", follow_redirects=False
    )

    state = parse_qs(
        urlparse(client.get("/static-info/admin/").headers["location"]).query
    )["state"][0]

    async def fake_exchange(code: str, code_verifier: str) -> dict:
        return {"access_token": _token(["global_admin"])}

    monkeypatch.setattr(admin_auth, "exchange_code", fake_exchange)
    res = client.get(
        "/static-info/admin-oauth/callback", params={"code": "c", "state": state}
    )
    assert res.status_code == 302
    return client


URL = "/static-info/admin/shuttle"


def _csrf(client: TestClient) -> str:
    html = client.get(URL, params={"new": "1"}).text
    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match
    return match.group(1)


def _save_form(csrf: str, place: str, **extra: str) -> dict[str, str]:
    form = {
        "csrf_token": csrf,
        "action": "save",
        "place": place,
        "title": "직접 등록 제목",
        "rows": "2",
        "t-0-direction": "본교 → 정왕역",
        "t-0-day_type": "평일",
        "t-0-departure_time": "18:10",
        "t-0-note": "막차",
        "t-1-departure_time": "",
    }
    return form | extra


def test_anonymous_redirected(admin: TestClient) -> None:
    admin.cookies.clear()
    assert admin.get(URL).status_code == 302


def test_post_without_csrf_rejected(admin: TestClient) -> None:
    res = admin.post(URL, data=_save_form("0" * 64, PLACE))
    assert res.status_code == 403
    assert st.load_store() == {}


def test_register_edit_delete(
    admin: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    csrf = _csrf(admin)
    res = admin.post(URL, data=_save_form(csrf, PLACE, is_new="1"))
    assert res.status_code == 303 and res.headers["location"].startswith(URL)
    doc = st.load_store()[PLACE]
    assert doc["source"] == "user" and doc["updated_by"] == "admin-sub"
    assert doc["title"] == "직접 등록 제목"
    assert [t["departure_time"] for t in doc["trips"]] == ["18:10"]
    assert doc["trips"][0]["destination"] == "정왕역"
    assert "admin_audit sub=admin-sub action=shuttle_save" in caplog.text

    listing = admin.get(URL)
    assert listing.status_code == 200 and PLACE in listing.text
    edit = admin.get(URL, params={"place": PLACE})
    assert edit.status_code == 200 and 'value="18:10"' in edit.text

    # 같은 이름으로 새 등록은 거부
    dup = admin.post(URL, data=_save_form(csrf, PLACE, is_new="1"))
    assert dup.status_code == 400

    # 편집: 첫 행 삭제 체크, 둘째 행 추가
    res = admin.post(
        URL,
        data=_save_form(
            csrf,
            PLACE,
            **{
                "t-0-delete": "1",
                "t-1-direction": "정왕역 → 본교",
                "t-1-day_type": "토요일",
                "t-1-departure_time": "09:00",
            },
        ),
    )
    assert res.status_code == 303
    assert [t["departure_time"] for t in st.load_store()[PLACE]["trips"]] == ["09:00"]

    res = admin.post(URL, data={"csrf_token": csrf, "action": "delete", "place": PLACE})
    assert res.status_code == 303 and PLACE not in st.load_store()
    assert "action=shuttle_delete" in caplog.text


def test_invalid_time_keeps_input(admin: TestClient) -> None:
    csrf = _csrf(admin)
    res = admin.post(
        URL, data=_save_form(csrf, PLACE, is_new="1", **{"t-0-departure_time": "25:00"})
    )
    assert res.status_code == 400 and 'value="25:00"' in res.text
    assert st.load_store() == {}


def test_sync_from_screen(admin: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sync = AsyncMock(return_value=["새 PDF"])
    monkeypatch.setattr(shuttle_view, "sync_shuttle_timetables", sync)
    res = admin.post(URL, data={"csrf_token": _csrf(admin), "action": "sync"})
    assert res.status_code == 303
    sync.assert_awaited_once_with(False)
    assert "새 PDF" in admin.get(res.headers["location"]).text


def test_menu_links_to_shuttle(admin: TestClient) -> None:
    assert "/static-info/admin/shuttle" in admin.get("/static-info/admin/").text
