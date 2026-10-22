"""TASK-138 리뷰 지적 회귀: 연도 포함 문서 선택, 괄호·★ 줄, 검증 경계, 캐시."""

import asyncio
import json
import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Config
from app.schemas.shuttle import ParsedTrip
from app.utils import shuttle_timetable as st
from app.utils.shuttle_pdf import _cam_body, _header_days
from main import app

api = TestClient(app)
D = datetime.date


@pytest.fixture(autouse=True)
def _store(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        Config, "SHUTTLE_TIMETABLE_PATH", str(tmp_path / "shuttle_timetable.json")
    )
    monkeypatch.setattr(Config, "SHUTTLE_PDF_DIR", str(tmp_path / "shuttle_pdf"))


def _trip(direction: str, time: str, vf: str = "09-01", vt: str = "12-22") -> dict:
    t = ParsedTrip(
        direction=direction,
        day_type="평일",
        day_note=None,
        departure_time=time,
        departure_time_end=None,
        valid_from=vf,
        valid_to=vt,
        period_note=None,
        boarding_place=None,
        note=None,
    )
    return st.ShuttleTrip(
        **t.model_dump(), **st.SHUTTLE_DIRECTIONS[direction]
    ).model_dump()


def _both(time: str) -> list[dict]:
    return [_trip("본교 → 정왕역", time), _trip("정왕역 → 본교", time)]


def test_last_year_user_doc_does_not_hide_this_year_pdf() -> None:
    store = {
        "25년 2학기(수정)": {
            "place": "25년 2학기(수정)",
            "source": "user",
            "ref_date": "2025-09-01",
            "updated_at": "2026-10-01T00:00:00+00:00",
            "trips": _both("07:00"),
        },
        "26년 2학기": {
            "place": "26년 2학기",
            "source": "pdf",
            "parsed_at": "2026-09-05T00:00:00+00:00",
            "trips": _both("08:00"),
        },
    }
    assert st.pick_document(store, D(2026, 10, 22))["place"] == "26년 2학기"
    assert st.pick_document(store, D(2025, 10, 22))["place"] == "25년 2학기(수정)"
    # 같은 날짜에 유효한 문서끼리는 수정본 우선
    store["26년 직접"] = {**store["26년 2학기"], "place": "26년 직접", "source": "user"}
    assert st.pick_document(store, D(2026, 10, 22))["place"] == "26년 직접"


def test_legacy_doc_year_from_title() -> None:
    """ref_date·parsed_at 없는 옛 저장본은 제목의 'NN년'으로 연도를 정합니다."""
    doc = {"place": "25년 셔틀 (2학기)", "source": "user", "trips": _both("07:00")}
    assert st.doc_range(doc, D(2026, 10, 22)) == (D(2025, 9, 1), D(2025, 12, 22))
    winter = {
        "place": "26년 겨울방학",
        "source": "pdf",
        "trips": [_trip("본교 → 정왕역", "09:00", "12-23", "02-28")],
    }
    assert st.doc_range(winter, D(2027, 1, 10)) == (D(2026, 12, 23), D(2027, 2, 28))


def test_out_of_period_shows_last_real_period() -> None:
    doc = {
        "place": "26년 2학기",
        "source": "pdf",
        "parsed_at": "2026-09-05T00:00:00+00:00",
        "trips": _both("08:00"),
    }
    body = st.build_timetable(
        st.pick_document({"a": doc}, D(2027, 1, 2)), D(2027, 1, 2)
    )
    assert (body["valid_from"], body["valid_to"]) == ("2026-09-01", "2026-12-22")
    assert all(not d["segments"] for r in body["routes"] for d in r["directions"])


def _w(x: float, top: float, text: str) -> dict:
    return {"x0": x, "x1": x + 20, "top": top, "text": text}


COLS = [(0.0, 100.0, "본교 → 제2캠퍼스"), (100.0, 200.0, "제2캠퍼스 → 본교")]
DAYS = [("평일", None), ("평일", None)]


def test_unclosed_paren_keeps_other_columns() -> None:
    out: list[dict] = []
    row = [_w(5, 10, "(정왕역출발"), _w(40, 10, "8:55,"), _w(120, 10, "10:30")]
    _cam_body(row, COLS, DAYS, out)
    assert [(t["direction"], t["departure_time"]) for t in out] == [
        ("제2캠퍼스 → 본교", "10:30")
    ]


def test_star_marked_times_kept_and_footnotes_dropped() -> None:
    out: list[dict] = []
    rows = [
        _w(5, 10, "★8:30"),
        _w(120, 10, "09:10"),
        _w(5, 30, "★"),
        _w(30, 30, "8:40"),
        _w(5, 50, "★"),
        _w(30, 50, "정왕역"),
        _w(60, 50, "12:00"),  # 각주가 시작된 열의 뒤쪽은 안내문
        _w(120, 50, "11:00"),  # 다른 열 시각은 각주와 무관하게 읽음
        _w(5, 70, "17:40"),
        _w(40, 70, "★"),
        _w(60, 70, "막차"),  # 줄 중간 각주형 ★
        _w(120, 70, "18:00"),
    ]
    _cam_body(rows, COLS, DAYS, out)
    assert sorted(t["departure_time"] for t in out) == [
        "08:30",
        "08:40",
        "09:10",
        "11:00",
        "17:40",
        "18:00",
    ]


def test_header_days_ignore_body_mentions() -> None:
    assert _header_days("평 일 토요일\n(일학습병행학부)") == {"평일", "토요일"}
    assert _header_days("『정왕역 ↔ 본교』시간표\n(평일, 토요일)") == {"평일", "토요일"}
    assert _header_days("※ 토요일 운휴, 공휴일 미운행") == set()


def test_semester_change_compares_with_latest_good() -> None:
    prev = {"trips": _both("08:00") + [_trip("본교 → 제2캠퍼스", "10:00")]}
    vacation = _both("09:00")
    # 편수는 줄어도(방학) 새 place라 급감으로 보지 않지만 노선 소실은 잡음
    assert st.validate_trips(vacation, [], prev, same_place=False) == [
        "노선 소실: 제2캠퍼스 ↔ 본교·정왕역"
    ]
    assert (
        st.validate_trips(
            vacation
            + [_trip("본교 → 제2캠퍼스", "10:00"), _trip("제2캠퍼스 → 본교", "11:00")],
            [],
            prev,
            same_place=False,
        )
        == []
    )


def _ingest(monkeypatch: pytest.MonkeyPatch, place: str, trips: list[dict]) -> Any:
    monkeypatch.setattr(
        st, "fetch_shuttle_pdf", AsyncMock(return_value=(place, "u", place.encode()))
    )
    parsed = [ParsedTrip(**t).model_dump() for t in trips]  # 파서 출력 모양
    monkeypatch.setattr(st, "parse_shuttle_pdf", lambda _: ("제목", parsed, [], []))
    return asyncio.run(st.ingest_shuttle_pdf())


def test_first_parse_failure_is_served_with_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _ingest(monkeypatch, "26년 2학기", [_trip("본교 → 정왕역", "08:00")])
    assert doc["problems"] and "pending" not in doc
    body = api.get("/bus/timetable", params={"date": "2026-10-22"}).json()
    assert body["source"]["needs_review"] is True


def test_semester_change_ingest_flags_lost_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = _both("08:00") + [
        _trip("본교 → 제2캠퍼스", "10:00"),
        _trip("제2캠퍼스 → 본교", "11:00"),
    ]
    assert "problems" not in _ingest(monkeypatch, "26년 1학기", full)
    doc = _ingest(monkeypatch, "26년 2학기", _both("08:00"))
    assert doc["problems"] == ["노선 소실: 제2캠퍼스 ↔ 본교·정왕역"]


def test_dateless_request_etag_is_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch, "26년 2학기", _both("08:00"))
    res = api.get("/bus/timetable")
    tag = res.headers["etag"]
    version = res.json()["version"]
    today = res.json()["date"].replace("-", "")
    assert tag == f'"{version}-{today}"'
    assert api.get("/bus/timetable", headers={"If-None-Match": tag}).status_code == 304
    # 어제 받은 태그(다른 날짜)나 날짜 없는 version으로는 304가 나지 않음
    for stale in (f'"{version}-20000101"', f'"{version}"'):
        got = api.get("/bus/timetable", headers={"If-None-Match": stale})
        assert got.status_code == 200


def test_time_format_validated() -> None:
    base = _trip("본교 → 정왕역", "08:00")
    for bad in ("8:30", "25:00", "08:60", "0830"):
        with pytest.raises(ValidationError):
            ParsedTrip(**{**base, "departure_time": bad})
    with pytest.raises(ValidationError):
        ParsedTrip(**{**base, "departure_time_end": "24:00", "kind": "frequent"})


def test_version_covers_response_fields() -> None:
    doc = {"place": "a", "source": "pdf", "pdf_url": "u1", "trips": _both("08:00")}
    v1 = st.timetable_version(doc, [], False)
    assert st.timetable_version({**doc, "pdf_url": "u2"}, [], False) != v1
    assert st.timetable_version({**doc, "parsed_at": "2026-01-01"}, [], False) != v1
    assert st.timetable_version({**doc, "pending": {"x": 1}}, [], False) == v1


def test_bad_stored_values_do_not_break_public_get(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """옛 저장값 'H:MM'은 고쳐 읽고, 해석 불가한 편만 건너뛰며 응답은 200."""
    good = _trip("정왕역 → 본교", "10:00")
    store = {
        "26년 2학기": {
            "place": "26년 2학기",
            "source": "pdf",
            "parsed_at": "2026-09-05T00:00:00+00:00",
            "trips": [
                {**good, "departure_time": "8:40"},
                {**good, "departure_time": "25:99"},
                {**good, "direction": "없는 방향"},
                good,
            ],
        }
    }
    Path(Config.SHUTTLE_TIMETABLE_PATH).write_text(
        json.dumps(store, ensure_ascii=False), encoding="utf-8"
    )
    res = api.get("/bus/timetable", params={"date": "2026-10-22"})
    assert res.status_code == 200
    main_in = res.json()["routes"][0]["directions"][1]["segments"]
    assert [g["time"] for g in main_in] == ["08:40", "10:00"]
    assert "해석 불가로 건너뜀" in caplog.text


def test_user_doc_wins_within_same_day_even_without_period() -> None:
    pdf = {
        "place": "26년 2학기",
        "source": "pdf",
        "parsed_at": "2026-09-05T00:00:00+00:00",
        "trips": _both("08:00"),
    }
    always = {
        "place": "직접(상시)",
        "source": "user",
        "ref_date": "2026-10-01",
        "trips": [{**t, "valid_from": None, "valid_to": None} for t in _both("07:00")],
    }
    assert st.pick_document({"a": pdf, "b": always}, D(2026, 10, 22)) is always
    # 기간 있는 관리자 문서도 그날 유효하면 우선, 기간 밖이면 PDF
    dated = {**always, "valid_from": "2026-11-01", "valid_to": "2026-11-30"}
    assert st.pick_document({"a": pdf, "b": dated}, D(2026, 11, 5)) is dated
    assert st.pick_document({"a": pdf, "b": dated}, D(2026, 10, 22)) is pdf
