"""TUK World 관리자 화면: 식당 CRUD, 좌표 검증, 이미지 업로드·삭제."""

import io
import os
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.admin import auth as admin_auth
from app.admin import mount_admin, tuk_world_view
from app.config import Config
from app.database import SessionLocal, init_db
from app.models.tuk_world import Restaurant, RestaurantImage
from app.utils.tuk_world_images import image_path

P = "/static-info/admin"
FORM = {
    "name": "맛집",
    "naver_map_url": "https://naver.me/x",
    "kakao_map_url": "",
    "latitude": "37.34",
    "longitude": "126.73",
    "discount_info": "10% 할인\n두 줄",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(Config, "ADMIN_PANEL_ENABLED", True)
    monkeypatch.setattr(Config, "KC_CLIENT_ID", "cid")
    monkeypatch.setattr(Config, "KC_CLIENT_SECRET", "secret")
    monkeypatch.setattr(Config, "STATIC_INFO_PUBLIC_BASE_URL", "https://x.test/s")
    monkeypatch.setattr(Config, "ADMIN_SESSION_KEY", Fernet.generate_key().decode())
    init_db()
    app = FastAPI(root_path=Config.ROOT_PATH)
    assert mount_admin(app) is not None
    # 로그인 흐름은 test_admin_auth.py가 검증한다. 여기서는 세션만 있다고 가정한다.
    monkeypatch.setattr(admin_auth, "admin_sub", lambda r: "admin-sub")
    monkeypatch.setattr(admin_auth, "is_full_admin", lambda r: True)
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    yield c
    with SessionLocal() as db:
        for r in db.scalars(select(Restaurant)).all():
            db.delete(r)
        db.commit()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    return buf.getvalue()


def _create(client: TestClient, **over: str) -> int:
    res = client.post(f"{P}/restaurant/create", data={**FORM, **over})
    assert res.status_code == 302, res.text
    with SessionLocal() as db:
        rid = db.scalars(select(Restaurant.id).order_by(Restaurant.id.desc())).first()
    assert rid is not None
    return rid


def test_crud_and_search(client: TestClient) -> None:
    rid = _create(client)
    with SessionLocal() as db:
        r = db.get(Restaurant, rid)
        assert r is not None
        assert (r.latitude, r.discount_info) == (37.34, "10% 할인\n두 줄")
        assert r.kakao_map_url is None
    res = client.post(f"{P}/restaurant/edit/{rid}", data={**FORM, "name": "새이름"})
    assert res.status_code == 302
    assert "새이름" in client.get(f"{P}/restaurant/list?search=새이").text
    assert "새이름" not in client.get(f"{P}/restaurant/list?search=zzz").text
    assert client.delete(f"{P}/restaurant/delete?pks={rid}").status_code == 200
    with SessionLocal() as db:
        assert db.get(Restaurant, rid) is None


@pytest.mark.parametrize(
    "over",
    [
        {"latitude": "90.1"},
        {"latitude": "-91"},
        {"longitude": "180.5"},
        {"longitude": "-181"},
        {"naver_map_url": "javascript:alert(1)"},
    ],
)
def test_invalid_input_rejected(client: TestClient, over: dict[str, str]) -> None:
    res = client.post(f"{P}/restaurant/create", data={**FORM, **over})
    assert res.status_code == 400


def _upload(client: TestClient, rid: int, data: bytes, name: str, mime: str):
    return client.post(
        f"{P}/restaurant-image/create",
        data={"restaurant": str(rid)},
        files={"file": (name, data, mime)},
    )


def test_image_upload_delete_and_restaurant_delete(client: TestClient) -> None:
    rid = _create(client)
    r0 = _upload(client, rid, _png(), "a.png", "image/png")
    assert r0.status_code == 302, r0.text[-600:]
    assert _upload(client, rid, _png(), "b.png", "image/png").status_code == 302
    with SessionLocal() as db:
        imgs = db.scalars(select(RestaurantImage)).all()
        assert len(imgs) == 2
        first, second = imgs[0].id, imgs[1].id
        paths = [image_path(i.filename) for i in imgs]
        assert all(
            os.path.exists(p) and os.path.basename(p) not in ("a.png", "b.png")
            for p in paths
        )
    assert str(first) in client.get(f"{P}/restaurant/details/{rid}").text
    assert client.delete(f"{P}/restaurant-image/delete?pks={first}").status_code == 200
    assert not os.path.exists(paths[0]) and os.path.exists(paths[1])
    assert client.delete(f"{P}/restaurant/delete?pks={rid}").status_code == 200
    assert not os.path.exists(paths[1])
    with SessionLocal() as db:
        assert db.get(RestaurantImage, second) is None


@pytest.mark.parametrize(
    "data,name,mime",
    [
        pytest.param(b"not an image", "a.png", "image/png", id="not-image"),
        pytest.param(_png(), "a.gif", "image/png", id="ext"),
        pytest.param(_png(), "a.png", "image/jpeg", id="mime"),
        pytest.param(b"", "a.png", "image/png", id="empty"),
        pytest.param(
            _png() + b"0" * (5 * 1024 * 1024), "a.png", "image/png", id="too-large"
        ),
    ],
)
def test_bad_image_rejected(
    client: TestClient, data: bytes, name: str, mime: str
) -> None:
    rid = _create(client)
    before = os.listdir(Config.TUK_WORLD_IMAGE_DIR)
    assert _upload(client, rid, data, name, mime).status_code == 400
    with SessionLocal() as db:
        assert db.scalars(select(RestaurantImage)).first() is None
    assert os.listdir(Config.TUK_WORLD_IMAGE_DIR) == before


def test_audit_log(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    rid = _create(client)
    assert any(
        "admin_audit" in m and "tuk_world.restaurant.create" in m and f"id={rid}" in m
        for m in caplog.messages
    )


def test_anonymous_redirected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin_auth, "admin_sub", lambda r: None)  # 세션 없음
    res = client.get(f"{P}/restaurant/list")
    assert res.status_code == 302


def test_category_address_form_and_filter(client: TestClient) -> None:
    rid = _create(client, category="술집", address="경기도 시흥시 어딘가 1")
    other = _create(client, category="식당", name="다른집")
    with SessionLocal() as db:
        r = db.get(Restaurant, rid)
        assert r is not None and (r.category, r.address) == (
            "술집",
            "경기도 시흥시 어딘가 1",
        )
        o = db.get(Restaurant, other)
        assert o is not None and o.address is None
    page = client.get(f"{P}/restaurant/list?category=술집").text
    assert "경기도 시흥시 어딘가 1" in page and "다른집" not in page
    assert "다른집" in client.get(f"{P}/restaurant/list?search=식당").text


def test_image_file_removed_when_commit_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqladmin._queries import Query

    async def fake_insert(self: Query, data: dict, request: object) -> None:
        # 훅(파일 저장)은 실행되고 커밋은 실패하는 상황
        await self.model_view.on_model_change(
            data, self.model_view.model(), True, request
        )
        raise RuntimeError("commit failed")

    rid = _create(client)
    before = os.listdir(Config.TUK_WORLD_IMAGE_DIR)
    monkeypatch.setattr(Query, "insert", fake_insert)
    saved: list[str] = []
    real_save = tuk_world_view.save_image

    def spy_save(data: bytes, ext: str) -> str:
        saved.append(real_save(data, ext))
        return saved[-1]

    monkeypatch.setattr(tuk_world_view, "save_image", spy_save)
    res = _upload(client, rid, _png(), "a.png", "image/png")
    assert res.status_code == 400  # sqladmin이 예외를 폼 오류로 바꾼다.
    assert len(saved) == 1  # 파일은 실제로 저장됐다가
    assert os.listdir(Config.TUK_WORLD_IMAGE_DIR) == before  # 지워졌다.
