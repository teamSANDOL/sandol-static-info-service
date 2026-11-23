"""학교 iBook 요청 형식(rawFileList)과 401·403·429 차단 래치, 호출부 처리를 확인합니다.

실제 네트워크는 쓰지 않고 httpx 호출을 모킹합니다.
"""

import asyncio
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.jobs import scheduler as sched
from app.schemas.shuttle_responses import REFRESH_BLOCKED_DETAIL
from app.services import shuttle_sync as syn
from app.utils import ibookdownloader as ibd
from app.utils.ibookdownloader import BookDownloader, RemoteAccessBlockedError
from app.utils.keycloak_auth import shuttle_admin_identity
from main import app

api = TestClient(app)
VIEWER_HTML = "<script>\nvar bookcode = 'ABC123';\n</script>"


@pytest.fixture(autouse=True)
def reset_latch() -> Iterator[None]:
    """테스트 사이 차단 래치를 비웁니다."""
    ibd._blocked_kinds.clear()
    yield
    ibd._blocked_kinds.clear()


def _response(status: int, text: str = "x") -> httpx.Response:
    request = httpx.Request("GET", "https://ibook.tukorea.ac.kr/x")
    return httpx.Response(status, request=request, text=text)


class _Recorder:
    """httpx.AsyncClient의 get/post를 가로채 호출 내용을 기록합니다."""

    def __init__(self, get_status: int = 200, post_status: int = 200) -> None:
        self.calls: list[tuple[str, str, Any, httpx.AsyncClient]] = []
        self.get_status = get_status
        self.post_status = post_status

    async def get(self, client: httpx.AsyncClient, url: str, **kw: Any) -> Any:
        self.calls.append(("GET", url, None, client))
        return _response(self.get_status, VIEWER_HTML)

    async def post(self, client: httpx.AsyncClient, url: str, **kw: Any) -> Any:
        self.calls.append(("POST", url, kw.get("data"), client))
        return _response(self.post_status, "<rawfiles/>")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()

    async def get(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return await rec.get(self, url, **kw)

    async def post(self: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
        return await rec.post(self, url, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return rec


def test_raw_file_list_request_shape(recorder: _Recorder) -> None:
    seen: list[httpx.Headers] = []
    original = httpx.AsyncClient.__init__

    def spy(self: httpx.AsyncClient, *a: Any, **kw: Any) -> None:
        original(self, *a, **kw)
        seen.append(self.headers)

    with patch.object(httpx.AsyncClient, "__init__", spy):
        xml = asyncio.run(BookDownloader().fetch_file_list())

    assert xml == "<rawfiles/>"
    (get_call, post_call) = recorder.calls
    assert get_call[:2] == ("GET", "https://ibook.tukorea.ac.kr/Viewer/menu02")
    assert post_call[:2] == ("POST", "https://ibook.tukorea.ac.kr/Viewer/rawFileList")
    assert post_call[2] == {"bookcode": "ABC123", "base64": "N"}  # key 없음
    assert get_call[3] is post_call[3]  # 같은 클라이언트(쿠키 유지)
    headers = seen[0]
    assert headers["x-requested-with"] == "XMLHttpRequest"
    assert headers["origin"] == "https://ibook.tukorea.ac.kr"
    assert headers["referer"] == "https://ibook.tukorea.ac.kr/Viewer/menu02"
    assert headers["user-agent"].startswith("Mozilla/5.0 (compatible; sandol-")


def test_403_on_raw_file_list_latches(
    recorder: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    recorder.post_status = 403
    downloader = BookDownloader()

    with caplog.at_level(logging.ERROR, logger="sandol_static_info_service"):
        with pytest.raises(RemoteAccessBlockedError):
            asyncio.run(downloader.fetch_file_list())
        sent = len(recorder.calls)
        with pytest.raises(RemoteAccessBlockedError):
            asyncio.run(downloader.fetch_file_list())

    assert len(recorder.calls) == sent  # 두 번째는 요청 0회
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1
    assert ibd._blocked_kinds == {"raw_file_list"}


def test_403_on_viewer_latches(recorder: _Recorder) -> None:
    recorder.get_status = 403
    for _ in range(2):
        with pytest.raises(RemoteAccessBlockedError):
            asyncio.run(BookDownloader().fetch_bookcode())
    assert len(recorder.calls) == 1
    assert ibd._blocked_kinds == {"viewer"}


def test_429_on_download_latches_only_download(recorder: _Recorder) -> None:
    recorder.get_status = 429
    for _ in range(2):
        with pytest.raises(RemoteAccessBlockedError):
            asyncio.run(BookDownloader().download_bytes("https://x/a.pdf"))
    assert len(recorder.calls) == 1
    assert ibd._blocked_kinds == {"download"}


def test_sync_skips_blocked_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    blocked = AsyncMock(side_effect=RemoteAccessBlockedError("raw_file_list", 403))
    with (
        patch.object(BookDownloader, "fetch_file_list", blocked),
        caplog.at_level(logging.WARNING, logger="sandol_static_info_service"),
    ):
        assert asyncio.run(syn.sync_shuttle_timetables()) == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)


def _as_admin() -> None:
    app.dependency_overrides[shuttle_admin_identity] = lambda: ("sub", ["global_admin"])


def test_refresh_returns_503_only_when_blocked() -> None:
    _as_admin()
    try:
        with patch("app.routers.bus.service.queue_sync") as queue:
            assert api.post("/bus/timetable/refresh").status_code == 202
            queue.assert_called_once_with(False)
            ibd._blocked_kinds.add("raw_file_list")
            res = api.post("/bus/timetable/refresh")
            assert res.status_code == 503
            assert res.json()["detail"] == REFRESH_BLOCKED_DETAIL
            queue.assert_called_once()  # 차단 후에는 큐에 넣지 않음
    finally:
        app.dependency_overrides.pop(shuttle_admin_identity, None)


def test_scheduler_registers_sync_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched.scheduler, "start", lambda: None)
    sched.start_scheduler()
    try:
        assert sched.scheduler.get_job("shuttle_timetable_sync") is not None
    finally:
        sched.scheduler.remove_all_jobs()
