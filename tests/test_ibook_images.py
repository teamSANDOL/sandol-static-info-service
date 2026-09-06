"""bus/meal 이미지 라우터가 같은 팩토리에서 올바른 iBook 주소로 만들어지는지 확인합니다."""

from fastapi.testclient import TestClient
import pytest

from app.config import Config
from app.utils import ibookdownloader
from main import app

client = TestClient(app)


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """BookDownloader.fetch_image_list를 가짜로 바꾸고 호출된 book url을 기록합니다."""
    called: list[str] = []

    async def _fetch(self: ibookdownloader.BookDownloader) -> list[str]:
        called.append(self.url)
        return ["https://img/1.jpg", "https://img/2.jpg"]

    monkeypatch.setattr(ibookdownloader.BookDownloader, "fetch_image_list", _fetch)
    return called


def test_meal_images_use_weekly_menu_book(fake_fetch: list[str]) -> None:
    response = client.get("/meal/images", headers={"Accept": "application/json"})

    assert response.status_code == 200
    assert response.json() == {"image_urls": ["https://img/1.jpg", "https://img/2.jpg"]}
    assert fake_fetch == [Config.WEEKLY_MENU_URL]


def test_bus_images_still_use_shuttle_book(fake_fetch: list[str]) -> None:
    response = client.get("/bus/image/2", headers={"Accept": "text/plain"})

    assert response.status_code == 200
    assert response.text == "https://img/2.jpg"
    assert fake_fetch == [Config.SHUTTLE_URL]


def test_out_of_range_index_and_bad_accept(fake_fetch: list[str]) -> None:
    assert (
        client.get("/meal/image/3", headers={"Accept": "text/plain"}).status_code == 404
    )
    assert (
        client.get("/meal/images", headers={"Accept": "image/png"}).status_code == 406
    )
