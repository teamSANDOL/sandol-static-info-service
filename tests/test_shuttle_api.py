"""GET /bus/timetable 계약 검증. 실제 PDF 고정 자료를 저장 파이프라인에 태웁니다."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.routers import bus
from app.utils import shuttle_timetable as st
from main import app

PDF = Path(__file__).parent / "fixtures" / "shuttle_2026_2nd.pdf"
PLACE = "26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22"
api = TestClient(app)


@pytest.fixture(autouse=True)
def _store(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        Config, "SHUTTLE_TIMETABLE_PATH", str(tmp_path / "shuttle_timetable.json")
    )
    monkeypatch.setattr(Config, "SHUTTLE_PDF_DIR", str(tmp_path / "shuttle_pdf"))


def _ingest(monkeypatch: pytest.MonkeyPatch, content: bytes | None = None) -> Any:
    fetch = AsyncMock(
        return_value=(PLACE, "https://x/shuttle.pdf", content or PDF.read_bytes())
    )
    monkeypatch.setattr(st, "fetch_shuttle_pdf", fetch)
    return asyncio.run(st.ingest_shuttle_pdf())


def _dirs(body: dict) -> dict[str, dict]:
    return {d["id"]: d for r in body["routes"] for d in r["directions"]}


def test_unavailable_and_bad_date() -> None:
    res = api.get("/bus/timetable")
    assert res.status_code == 503 and res.json() == {"detail": "timetable_unavailable"}
    assert api.get("/bus/timetable", params={"date": "2026-13-01"}).status_code == 422


def test_weekday_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch)
    res = api.get("/bus/timetable", params={"date": "2026-10-22"})
    assert res.status_code == 200
    body = res.json()
    assert body["date"] == "2026-10-22" and body["day_type"] == "weekday"
    assert (body["valid_from"], body["valid_to"]) == ("2026-09-01", "2026-12-22")
    assert body["source"]["title"] == PLACE
    assert body["source"]["origin"] == "pdf" and body["source"]["needs_review"] is False
    assert [s["id"] for s in body["stops"]] == ["main_campus", "jeongwang", "campus2"]
    assert [r["id"] for r in body["routes"]] == ["main", "campus2"]
    d = _dirs(body)
    assert d["campus2_from_station"]["via"] == ["main_campus"]
    assert (d["main_in"]["from"], d["main_in"]["to"]) == ("jeongwang", "main_campus")
    segs = d["main_in"]["segments"]
    assert segs[0] == {
        "type": "frequent",
        "start": "08:40",
        "end": "10:00",
        "note": "수시운행",
    }
    assert segs[1] == {"type": "fixed", "time": "10:00", "note": None}
    assert segs[-1] == {
        "type": "arrival_board",
        "start": "17:00",
        "end": "22:17",
        "note": "도착버스 탑승",
    }
    assert len(d["main_out"]["segments"]) == 56
    assert d["campus2_to_station"]["segments"] == []  # 토요일 전용
    assert [g["time"] for g in d["campus2_from_station"]["segments"]] == [
        "08:55",
        "09:00",
    ]
    assert body["notes"][0].startswith("소요시간 10분")


def test_saturday_sunday_holiday(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch)
    sat = api.get("/bus/timetable", params={"date": "2026-10-24"}).json()
    d = _dirs(sat)
    assert sat["day_type"] == "saturday" and d["main_in"]["segments"] == []
    assert len(d["campus2_to_station"]["segments"]) == 7
    assert d["campus2_from_station"]["segments"][0] == {
        "type": "fixed",
        "time": "08:45",
        "note": "일학습병행학부",
    }
    sun = api.get("/bus/timetable", params={"date": "2026-10-25"}).json()
    assert sun["day_type"] == "sunday"
    assert all(not x["segments"] for x in _dirs(sun).values())
    hol = api.get("/bus/timetable", params={"date": "2026-10-05"}).json()
    assert (hol["day_type"], hol["holiday_name"]) == ("holiday", "개천절 대체공휴일")
    assert all(not x["segments"] for x in _dirs(hol).values())
    assert sat["version"] == sun["version"] == hol["version"]


def test_out_of_period_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch)
    body = api.get("/bus/timetable", params={"date": "2026-12-28"}).json()
    assert body["day_type"] == "weekday"
    assert all(not x["segments"] for x in _dirs(body).values())
    assert len(_dirs(body)) == 6


def test_version_tracks_holiday_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """공휴일 파일만 바뀌어도 version이 바뀌어야 앱이 304로 옛 day_type을 쓰지 않습니다."""
    _ingest(monkeypatch)
    before = api.get("/bus/timetable", params={"date": "2026-10-22"}).json()
    src = Path(Config.HOLIDAYS_PATH).read_text(encoding="utf-8")
    changed = tmp_path / "holidays.json"
    changed.write_text(
        src.replace('"신정",', '"신정", "2026-10-22": "임시공휴일",', 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(Config, "HOLIDAYS_PATH", str(changed))
    after = api.get("/bus/timetable", params={"date": "2026-10-22"}).json()
    assert (after["day_type"], after["holiday_name"]) == ("holiday", "임시공휴일")
    assert after["version"] != before["version"]


def test_validation_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _ingest(monkeypatch)
    trips = good["trips"]
    assert "problems" not in good and "pending" not in good
    assert st.validate_trips(trips, ["평일", "토요일"], good) == []

    one_way = [t for t in trips if t["direction"] != "정왕역 → 본교"]
    assert "한쪽 방향" in st.validate_trips(one_way, [], None)[0]
    weekday_only = [t for t in trips if t["day_type"] != "토요일"]
    assert st.validate_trips(weekday_only, ["토요일"], None) == [
        "표 머리에 '토요일'이 있는데 토요일 운행편이 없습니다"
    ]
    assert st.validate_trips(weekday_only, ["평일"], None) == []
    main = [t for t in trips if t["route"] == "정왕역 ↔ 본교"][:10] + [
        t for t in trips if t["direction"] == "정왕역 → 본교"
    ][:2]
    assert st.validate_trips(main, [], good) == [
        "노선 소실: 제2캠퍼스 ↔ 본교·정왕역",
        f"편수 급감: {len(trips)} → 12",
    ]
    reversed_range = [
        {**t, "departure_time_end": "08:00"} if t["kind"] != "fixed" else t
        for t in trips
    ]
    assert st.validate_trips(reversed_range, [], None)[0].startswith("구간 시작")
    assert st.validate_trips([], [], None) == ["운행편이 하나도 없습니다"]


def test_failed_parse_keeps_previous(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _ingest(monkeypatch)
    before = api.get("/bus/timetable", params={"date": "2026-10-24"}).json()
    title, trips, notes, _ = st.parse_shuttle_pdf(PDF.read_bytes())
    broken = [t for t in trips if t["day_type"] != "토요일"]  # 토요일 열을 놓친 파싱
    monkeypatch.setattr(
        st, "parse_shuttle_pdf", lambda _: (title, broken, notes, ["토요일"])
    )
    doc = _ingest(monkeypatch, b"%PDF new version")
    assert doc["pending"]["problems"] and "검토 대기" in caplog.text
    after = api.get("/bus/timetable", params={"date": "2026-10-24"}).json()
    assert after["source"]["needs_review"] is True
    assert after["routes"] == before["routes"]  # 직전 정상본을 그대로 내보냄
    assert after["version"] != before["version"]
    # 같은 PDF가 다시 와도 검토 대기본과 해시가 같아 다시 파싱하지 않음
    assert _ingest(monkeypatch, b"%PDF new version") is None


def test_user_override_clears_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch)
    title, trips, notes, _ = st.parse_shuttle_pdf(PDF.read_bytes())
    monkeypatch.setattr(st, "parse_shuttle_pdf", lambda _: (title, [], notes, []))
    _ingest(monkeypatch, b"%PDF empty")
    assert st.needs_review(st.load_store()[PLACE])
    kept = [st.ParsedTrip(**t) for t in st.load_store()[PLACE]["trips"]]
    asyncio.run(st.save_user_timetable(PLACE, kept, "직접"))
    body = api.get("/bus/timetable", params={"date": "2026-10-22"}).json()
    assert body["source"]["origin"] == "user"
    assert body["source"]["needs_review"] is False
    # 사용자 수정본은 force 없는 동기화가 건드리지 않음
    assert _ingest(monkeypatch, b"%PDF another") is None


def test_etag_and_304(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch)
    res = api.get("/bus/timetable", params={"date": "2026-10-22"})
    version = res.json()["version"]
    assert res.headers["etag"] == f'"{version}"'
    assert res.headers["cache-control"] == "public, max-age=300"

    for tag in (f'"{version}"', f'W/"{version}"', f'"other", "{version}"', "*"):
        hit = api.get(
            "/bus/timetable",
            params={"date": "2026-10-22"},
            headers={"If-None-Match": tag},
        )
        assert hit.status_code == 304 and hit.content == b""
        assert hit.headers["etag"] == f'"{version}"'
        assert hit.headers["cache-control"] == "public, max-age=300"

    for tag in ('"stale"', version):  # 따옴표 없는 값은 ETag 형식이 아님
        miss = api.get(
            "/bus/timetable",
            params={"date": "2026-10-22"},
            headers={"If-None-Match": tag},
        )
        assert miss.status_code == 200 and miss.json()["version"] == version
    # 503에는 캐시 헤더를 붙이지 않음
    monkeypatch.setattr(bus, "load_store", dict)
    gone = api.get("/bus/timetable")
    assert gone.status_code == 503 and "etag" not in gone.headers
