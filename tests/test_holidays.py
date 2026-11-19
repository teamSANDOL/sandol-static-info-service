"""공휴일 API 클라이언트·캐시 검증. HTTP는 전부 MockTransport로 막고 실제 네트워크는 타지 않습니다."""

import asyncio
import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Config
from app.services import holidays as h
from app.services import shuttle_schedule as sch

KEY = "SECRET%2BKEY%3D%3D"
REAL_CLIENT = httpx.AsyncClient


def _item(d: int, name: str, is_holiday: str = "Y") -> dict[str, Any]:
    return {"dateKind": "01", "dateName": name, "isHoliday": is_holiday, "locdate": d}


def _ok(items: Any, total: int | None = None) -> dict[str, Any]:
    if total is None:
        total = len(items) if isinstance(items, list) else int(bool(items))
    body: dict[str, Any] = {"items": "" if not items else {"item": items}}
    body.update(numOfRows=100, pageNo=1, totalCount=total)
    return {
        "response": {"header": {"resultCode": "00", "resultMsg": "OK"}, "body": body}
    }


class Api:
    """요청을 기록하고 정해진 응답을 돌려주는 가짜 포털."""

    def __init__(self, handler: Any) -> None:
        self.calls: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._handler(request)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "HOLIDAY_API_KEY", KEY)
    monkeypatch.setattr(h, "_memory", {})
    monkeypatch.setattr(h, "_state", {"loaded": False})
    monkeypatch.setattr(h, "today_year", lambda: 2026)


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> Api:
    api = Api(handler)
    monkeypatch.setattr(
        h.httpx,
        "AsyncClient",
        lambda timeout: REAL_CLIENT(
            timeout=timeout, transport=httpx.MockTransport(api)
        ),
    )
    return api


def _json(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _fetch(handler: Any, year: int = 2026) -> dict[str, str]:
    async def run() -> dict[str, str]:
        transport = httpx.MockTransport(handler)
        async with REAL_CLIENT(transport=transport) as client:
            return await h.fetch_year(client, year, KEY)

    return asyncio.run(run())


def test_fetch_many_single_and_empty() -> None:
    many = _fetch(
        lambda r: _json(
            _ok(
                [
                    _item(20260101, "신정"),
                    _item(20260301, "삼일절"),
                    _item(20260317, "임시", "N"),
                ]
            )
        )
    )
    assert many == {"2026-01-01": "신정", "2026-03-01": "삼일절"}  # isHoliday=N 제외
    single = _fetch(
        lambda r: _json(_ok(_item(20300101, "신정"), 1)), 2030
    )  # 1건은 객체
    assert single == {"2030-01-01": "신정"}
    assert _fetch(lambda r: _json(_ok("", 0)), 2031) == {}  # 0건은 items가 빈 문자열


def test_fetch_reads_next_page() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        page = int(r.url.params["pageNo"])
        rows = [_item(20260101 + page, f"p{page}")]
        return _json(_ok(rows, 150))

    assert len(_fetch(handler)) == 2


def test_fetch_sends_key_once_encoded() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return _json(_ok([_item(20260101, "신정")]))

    _fetch(handler)
    assert r"SECRET%2BKEY%3D%3D" in str(seen[0].url)  # 풀어서 한 번만 인코딩
    assert seen[0].url.params["ServiceKey"] == "SECRET+KEY=="
    assert seen[0].url.params["_type"] == "json"
    assert seen[0].url.params["solYear"] == "2026"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            text="<OpenAPI_ServiceResponse><cmmMsgHeader/></OpenAPI_ServiceResponse>",
        ),
        httpx.Response(
            403,
            json={
                "OpenAPI_ServiceResponse": {
                    "cmmMsgHeader": {"errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}
                }
            },
        ),
        httpx.Response(
            200, json={"response": {"header": {"resultCode": "30", "resultMsg": "ERR"}}}
        ),
        httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {"items": {"item": [{"x": 1}]}, "totalCount": 1},
                }
            },
        ),
        httpx.Response(200, json=[1, 2]),
    ],
)
def test_fetch_rejects_bad_responses(response: httpx.Response) -> None:
    with pytest.raises(ValueError) as err:
        _fetch(lambda r: response)
    assert "SECRET" not in str(err.value)


def test_refresh_without_key_does_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Config, "HOLIDAY_API_KEY", "")
    api = _serve(monkeypatch, lambda r: _json(_ok([])))
    asyncio.run(h.refresh_holidays())
    assert api.calls == [] and h.api_holidays() == {}
    assert not (tmp_path / "holidays").exists()


def test_refresh_caches_reuses_and_refreshes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _serve(
        monkeypatch,
        lambda r: _json(
            _ok([_item(int(r.url.params["solYear"]) * 10000 + 1022, "임시공휴일")])
        ),
    )
    asyncio.run(h.refresh_holidays())
    assert [c.url.params["solYear"] for c in api.calls] == ["2026", "2027"]
    assert h.api_holidays() == {"2026-10-22": "임시공휴일", "2027-10-22": "임시공휴일"}
    assert (tmp_path / "holidays" / "2026.json").exists()

    asyncio.run(h.refresh_holidays())  # 하루 안에는 다시 받지 않는다
    assert len(api.calls) == 2

    for year in (2026, 2027):  # 하루가 지나면 올해·내년은 다시 받는다
        p = tmp_path / "holidays" / f"{year}.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["fetched_at"] = time.time() - h.REFRESH_SECONDS - 1
        p.write_text(json.dumps(doc), encoding="utf-8")
    asyncio.run(h.refresh_holidays())
    assert len(api.calls) == 4


def test_cache_survives_restart_and_past_year_is_not_refetched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(h, "today_year", lambda: 2027)
    old = tmp_path / "holidays"
    old.mkdir()
    (old / "2026.json").write_text(
        json.dumps({"fetched_at": 1.0, "dates": {"2026-01-01": "신정"}}),
        encoding="utf-8",
    )
    api = _serve(monkeypatch, lambda r: _json(_ok([_item(20270101, "신정")])))
    asyncio.run(h.refresh_holidays())
    assert [c.url.params["solYear"] for c in api.calls] == [
        "2027",
        "2028",
    ]  # 2026은 안 받음
    assert h.api_holidays()["2026-01-01"] == "신정"  # 디스크 캐시를 읽음


def test_zero_items_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = _serve(monkeypatch, lambda r: _json(_ok("", 0)))
    asyncio.run(h.refresh_holidays())
    assert len(api.calls) == 2 and h.api_holidays() == {}
    assert not (tmp_path / "holidays").exists()
    asyncio.run(h.refresh_holidays())  # 영구 캐시가 아니므로 다음 갱신에서 다시 시도
    assert len(api.calls) == 4


def test_failures_keep_old_cache_and_never_log_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    h._memory[2026] = {"2026-01-01": "신정"}

    def boom(r: httpx.Request) -> httpx.Response:
        if r.url.params["solYear"] == "2026":
            raise httpx.ReadTimeout("timeout", request=r)
        return httpx.Response(200, text="<OpenAPI_ServiceResponse/>")

    _serve(monkeypatch, boom)
    asyncio.run(h.refresh_holidays())  # 예외를 밖으로 내지 않는다
    assert h.api_holidays() == {"2026-01-01": "신정"}
    assert "공휴일 API 실패" in caplog.text
    assert "SECRET" not in caplog.text and "ServiceKey" not in caplog.text


def test_concurrent_refresh_fetches_each_year_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _serve(
        monkeypatch,
        lambda r: _json(
            _ok([_item(int(r.url.params["solYear"]) * 10000 + 101, "신정")])
        ),
    )

    async def run() -> None:
        await asyncio.gather(*(h.refresh_holidays() for _ in range(5)))

    asyncio.run(run())
    assert len(api.calls) == 2


def test_day_type_uses_union_of_api_and_static() -> None:
    h._memory[2026] = {"2026-10-22": "임시공휴일", "2026-01-01": "API 신정"}
    h._state["loaded"] = True
    assert sch.day_type_of(date(2026, 10, 22)) == ("holiday", "임시공휴일")
    assert sch.day_type_of(date(2026, 6, 3)) == (
        "holiday",
        "전국동시지방선거",
    )  # 정적에만 있는 날
    assert sch.day_type_of(date(2026, 1, 1)) == (
        "holiday",
        "신정",
    )  # 같은 날짜는 정적 이름
    h._memory[2030] = {"2030-01-01": "신정"}
    assert sch.day_type_of(date(2030, 1, 1)) == ("holiday", "신정")
    assert os.environ.get("HOLIDAY_API_KEY") != KEY
