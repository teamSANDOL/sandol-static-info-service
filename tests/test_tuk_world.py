"""TUK World API: 공개 조회, 쓰기 권한(fail-closed 포함), 이미지 업로드 검증."""

import io
import os
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Config
from app.database import init_db
from app.utils import keycloak_auth
from main import app

client = TestClient(app)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
BODY = {
    "name": "[더미] 테스트 식당",
    "naver_map_url": "https://map.naver.com/p/entry/place/0",
    "kakao_map_url": "https://place.map.kakao.com/0",
    "latitude": 37.34,
    "longitude": 126.733,
    "discount_info": "학생증 제시 시 10% 할인\n포장 제외",
}


class _FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("K", (), {"key": KEY.public_key()})()


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    monkeypatch.setattr(keycloak_auth, "_jwks_client", lambda: _FakeJwks())


def token(
    roles: list[str],
    key: Any = KEY,
    iss: str | None = None,
    **claims: Any,
) -> dict[str, str]:
    payload = {
        "sub": "admin-sub",
        "iss": iss or keycloak_auth.expected_issuer(),
        "exp": int(time.time()) + 300,
        "realm_access": {"roles": roles},
        **claims,
    }
    return {"Authorization": f"Bearer {jwt.encode(payload, key, algorithm='RS256')}"}


ADMIN = token(["global_admin"])


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return buf.getvalue()


def create() -> int:
    res = client.post("/tuk-world/restaurants", json=BODY, headers=ADMIN)
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


def test_public_read_and_admin_crud() -> None:
    rid = create()
    listed = client.get("/tuk-world/restaurants").json()
    assert any(
        r["id"] == rid and r["discount_info"] == BODY["discount_info"] for r in listed
    )
    assert client.get("/tuk-world/restaurants/999999").status_code == 404

    res = client.patch(
        f"/tuk-world/restaurants/{rid}", json={"discount_info": None}, headers=ADMIN
    )
    assert res.status_code == 200 and res.json()["discount_info"] is None
    res = client.patch(
        f"/tuk-world/restaurants/{rid}", json={"name": None}, headers=ADMIN
    )
    assert res.status_code == 422

    assert (
        client.delete(f"/tuk-world/restaurants/{rid}", headers=ADMIN).status_code == 204
    )
    assert client.get(f"/tuk-world/restaurants/{rid}").status_code == 404


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer not-a-jwt"}, 401),
        (token(["global_admin"], key=OTHER_KEY), 401),
        (token(["global_admin"], iss="https://evil/realms/Sandori"), 401),
        (token(["user"]), 403),
    ],
)
def test_write_rejected(headers: dict[str, str], status: int) -> None:
    assert (
        client.post("/tuk-world/restaurants", json=BODY, headers=headers).status_code
        == status
    )


def test_write_fail_closed_without_keycloak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Config, "KC_SERVER_URL", "")
    res = client.post("/tuk-world/restaurants", json=BODY, headers=ADMIN)
    assert res.status_code == 503


def test_image_upload_serve_delete() -> None:
    rid = create()
    url = f"/tuk-world/restaurants/{rid}/images"
    data = png_bytes()
    res = client.post(
        url, files={"file": ("../../evil.png", data, "image/png")}, headers=ADMIN
    )
    assert res.status_code == 201, res.text
    image = res.json()["images"][0]
    assert image["url"].endswith(f"/tuk-world/images/{image['id']}")
    saved = os.listdir(Config.TUK_WORLD_IMAGE_DIR)
    assert "evil.png" not in saved and all(len(n) == 36 for n in saved)

    got = client.get(f"/tuk-world/images/{image['id']}")
    assert got.status_code == 200 and got.content == data
    assert got.headers["content-type"] == "image/png"

    assert (
        client.post(url, files={"file": ("a.png", data, "image/png")}).status_code
        == 401
    )
    assert (
        client.delete(f"/tuk-world/images/{image['id']}", headers=ADMIN).status_code
        == 204
    )
    assert client.get(f"/tuk-world/images/{image['id']}").status_code == 404


@pytest.mark.parametrize(
    ("name", "data", "ctype", "status"),
    [
        ("a.png", b"not an image", "image/png", 415),
        ("a.svg", png_bytes(), "image/png", 415),
        ("a.png", png_bytes(), "image/jpeg", 415),
        ("a.png", b"", "image/png", 415),
    ],
)
def test_image_upload_rejected(name: str, data: bytes, ctype: str, status: int) -> None:
    rid = create()
    res = client.post(
        f"/tuk-world/restaurants/{rid}/images",
        files={"file": (name, data, ctype)},
        headers=ADMIN,
    )
    assert res.status_code == status


def test_image_upload_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    rid = create()
    monkeypatch.setattr(Config, "TUK_WORLD_MAX_IMAGE_BYTES", 10)
    res = client.post(
        f"/tuk-world/restaurants/{rid}/images",
        files={"file": ("a.png", png_bytes(), "image/png")},
        headers=ADMIN,
    )
    assert res.status_code == 413


def test_timestamps_are_utc_aware() -> None:
    rid = create()
    body = client.get(f"/tuk-world/restaurants/{rid}").json()
    for value in (body["created_at"], body["updated_at"]):
        assert value.endswith(("Z", "+00:00")), value


def test_image_upload_and_delete_touch_restaurant_updated_at() -> None:
    rid = create()
    url = f"/tuk-world/restaurants/{rid}"
    before = client.get(url).json()["updated_at"]
    time.sleep(0.01)
    res = client.post(
        f"{url}/images",
        files={"file": ("a.png", png_bytes(), "image/png")},
        headers=ADMIN,
    )
    uploaded = res.json()["updated_at"]
    assert uploaded > before
    time.sleep(0.01)
    image_id = res.json()["images"][0]["id"]
    client.delete(f"/tuk-world/images/{image_id}", headers=ADMIN)
    assert client.get(url).json()["updated_at"] > uploaded


def test_public_base_url_makes_image_url_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid = create()
    monkeypatch.setattr(Config, "PUBLIC_BASE_URL", "https://sandori.kr")
    res = client.post(
        f"/tuk-world/restaurants/{rid}/images",
        files={"file": ("a.png", png_bytes(), "image/png")},
        headers=ADMIN,
    )
    url = res.json()["images"][0]["url"]
    assert url.startswith("https://sandori.kr") and "/tuk-world/images/" in url


@pytest.mark.parametrize(
    ("claims", "status"),
    [
        ({"azp": "kakao-bot"}, 201),
        ({"aud": "kakao-bot"}, 201),
        ({"aud": ["account", "kakao-bot"]}, 201),
        ({"azp": "other", "aud": ["account"]}, 401),
        ({}, 401),
    ],
)
def test_allowed_clients(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any], status: int
) -> None:
    monkeypatch.setattr(Config, "KC_ALLOWED_CLIENTS", ["kakao-bot"])
    res = client.post(
        "/tuk-world/restaurants",
        json=BODY,
        headers=token(["global_admin"], **claims),
    )
    assert res.status_code == status


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/bus/timetable/refresh"),
        ("PUT", "/bus/timetable/x"),
        ("DELETE", "/bus/timetable/x"),
    ],
)
def test_bus_write_requires_token(method: str, path: str) -> None:
    res = client.request(method, path, json={"trips": []} if method == "PUT" else None)
    assert res.status_code == 401
