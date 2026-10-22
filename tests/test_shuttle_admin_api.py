"""셔틀 시간표 관리자 API(/bus/admin/timetables): 권한·CRUD·검증·승인 흐름."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import Config
from app.routers import bus
from app.utils import keycloak_auth
from app.utils import shuttle_timetable as st
from main import app

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
BASE = "/bus/admin/timetables"
PLACE = "26년 2학기 직접"
PDF = Path(__file__).parent / "fixtures" / "shuttle_2026_2nd.pdf"
PDF_PLACE = "26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22"
api = TestClient(app)


class _FakeJwks:
    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("K", (), {"key": KEY.public_key()})()


def _auth(roles: list[str], sub: str = "shuttle-sub") -> dict[str, str]:
    payload = {
        "sub": sub,
        "iss": keycloak_auth.expected_issuer(),
        "exp": int(time.time()) + 300,
        "realm_access": {"roles": roles},
    }
    return {"Authorization": f"Bearer {jwt.encode(payload, KEY, algorithm='RS256')}"}


ADMIN = _auth(["shuttle_admin"])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        Config, "SHUTTLE_TIMETABLE_PATH", str(tmp_path / "shuttle_timetable.json")
    )
    monkeypatch.setattr(Config, "SHUTTLE_PDF_DIR", str(tmp_path / "shuttle_pdf"))
    monkeypatch.setattr(keycloak_auth, "_jwks_client", lambda: _FakeJwks())


TRIP = {"direction": "main_in", "day_type": "평일", "time": "08:00"}
BODY = {
    "place": PLACE,
    "title": "2학기 직접 입력",
    "valid_from": "2026-09-01",
    "valid_to": "2026-12-22",
    "notes": ["소요시간 10분"],
    "trips": [
        TRIP,
        {"direction": "main_out", "day_type": "평일", "time": "09:00"},
        {
            "direction": "main_in",
            "day_type": "평일",
            "kind": "arrival_board",
            "time": "17:00",
            "end": "22:17",
            "note": "도착버스 탑승",
        },
    ],
}


def _public(day: str = "2026-10-22") -> dict:
    res = api.get("/bus/timetable", params={"date": day})
    assert res.status_code == 200, res.text
    return res.json()


def _main_in(body: dict) -> list[dict]:
    return body["routes"][0]["directions"][1]["segments"]


def test_auth_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    assert api.get(BASE).status_code == 401
    assert api.post(BASE, json=BODY).status_code == 401
    assert api.get(BASE, headers=_auth(["default-roles-sandori"])).status_code == 403
    assert api.post(BASE, json=BODY, headers=_auth(["other"])).status_code == 403
    assert api.get(BASE, headers=ADMIN).status_code == 200
    assert api.get(BASE, headers=_auth(["global_admin"])).status_code == 200
    # 기존 셔틀 쓰기 경로도 shuttle_admin 허용
    res = api.put(
        f"/bus/timetable/{PLACE}",
        json={"title": "t", "trips": []},
        headers=ADMIN,
    )
    assert res.status_code == 200
    # 역할 목록은 설정값
    monkeypatch.setattr(Config, "SHUTTLE_ADMIN_ROLES", ["global_admin"])
    assert api.get(BASE, headers=ADMIN).status_code == 403


def test_other_admin_paths_still_need_global_admin() -> None:
    res = api.delete("/tuk-world/restaurants/1", headers=_auth(["shuttle_admin"]))
    assert res.status_code == 403


def test_crud_flow_reflects_public(caplog: pytest.LogCaptureFixture) -> None:
    res = api.post(BASE, json=BODY, headers=ADMIN)
    assert res.status_code == 201, res.text
    doc = res.json()
    assert doc["source"] == "user" and doc["trip_count"] == 3
    assert (doc["valid_from"], doc["valid_to"]) == ("2026-09-01", "2026-12-22")
    ids = [t["id"] for t in doc["trips"]]
    assert len(set(ids)) == 3
    assert api.post(BASE, json=BODY, headers=ADMIN).status_code == 409
    assert "admin_audit sub=shuttle-sub action=shuttle_admin.create" in caplog.text

    listed = api.get(BASE, headers=ADMIN).json()
    assert [d["place"] for d in listed] == [PLACE]
    assert listed[0]["needs_review"] is False

    before = _public()
    assert [g.get("time") or g["start"] for g in _main_in(before)] == ["08:00", "17:00"]
    assert _public("2027-01-05")["valid_to"] == "2026-12-22"  # 연도 포함 기간

    # 운행편 추가
    add = api.post(
        f"{BASE}/{PLACE}/trips",
        json={"direction": "main_in", "day_type": "평일", "time": "08:30"},
        headers=ADMIN,
    )
    assert add.status_code == 201
    new_id = add.json()["id"]
    after_add = _public()
    assert [g.get("time") or g["start"] for g in _main_in(after_add)] == [
        "08:00",
        "08:30",
        "17:00",
    ]
    assert after_add["version"] != before["version"]

    # 운행편 수정(id 유지)
    upd = api.put(
        f"{BASE}/{PLACE}/trips/{new_id}",
        json={
            "direction": "main_in",
            "day_type": "평일",
            "time": "08:45",
            "note": "증편",
        },
        headers=ADMIN,
    )
    assert upd.status_code == 200 and upd.json()["id"] == new_id
    assert {"type": "fixed", "time": "08:45", "note": "증편"} in _main_in(_public())
    detail = api.get(f"{BASE}/{PLACE}", headers=ADMIN).json()
    assert new_id in [t["id"] for t in detail["trips"]]
    assert set(ids) <= {t["id"] for t in detail["trips"]}  # 다른 편 id는 그대로

    # 운행편 삭제
    assert (
        api.delete(f"{BASE}/{PLACE}/trips/{new_id}", headers=ADMIN).status_code == 204
    )
    assert (
        api.delete(f"{BASE}/{PLACE}/trips/{new_id}", headers=ADMIN).status_code == 404
    )

    # 메타 수정: 기간을 바꾸면 공개 응답도 바뀜
    patch = api.patch(
        f"{BASE}/{PLACE}",
        json={"title": "새 제목", "valid_from": "2026-11-01", "valid_to": "2026-12-22"},
        headers=ADMIN,
    )
    assert patch.status_code == 200 and patch.json()["title"] == "새 제목"
    assert _public("2026-10-22")["valid_from"] == "2026-11-01"
    assert _main_in(_public("2026-10-22")) == []  # 이제 기간 밖
    bad = api.patch(f"{BASE}/{PLACE}", json={"valid_from": "2026-11-01"}, headers=ADMIN)
    assert bad.status_code == 422

    assert api.delete(f"{BASE}/{PLACE}", headers=ADMIN).status_code == 204
    assert api.get(f"{BASE}/{PLACE}", headers=ADMIN).status_code == 404
    assert api.get("/bus/timetable").status_code == 503


@pytest.mark.parametrize(
    "trip",
    [
        {"direction": "main_in", "day_type": "평일", "time": "8:00"},
        {"direction": "main_in", "day_type": "평일", "time": "24:00"},
        {"direction": "본교 → 정왕역", "day_type": "평일", "time": "08:00"},
        {"direction": "campus3_in", "day_type": "평일", "time": "08:00"},
        {"direction": "main_in", "day_type": "weekday", "time": "08:00"},
        {"direction": "main_in", "day_type": "평일", "time": "08:00", "kind": "x"},
        {"direction": "main_in", "day_type": "평일", "time": "08:00", "end": "09:00"},
        {
            "direction": "main_in",
            "day_type": "평일",
            "time": "18:00",
            "kind": "frequent",
        },
        {
            "direction": "main_in",
            "day_type": "평일",
            "time": "18:00",
            "end": "17:00",
            "kind": "frequent",
        },
        {"direction": "main_in", "day_type": "평일", "time": "08:00", "extra": 1},
    ],
)
def test_invalid_trip_422(trip: dict) -> None:
    assert (
        api.post(BASE, json={**BODY, "trips": [trip]}, headers=ADMIN).status_code == 422
    )


def test_invalid_meta_422() -> None:
    for meta in (
        {"valid_from": "2026-12-22", "valid_to": "2026-09-01"},
        {"valid_from": "2026-09-01", "valid_to": None},
        {"valid_from": "26-09-01", "valid_to": "2026-12-22"},
        {"place": ""},
    ):
        assert api.post(BASE, json={**BODY, **meta}, headers=ADMIN).status_code == 422


def _ingest(monkeypatch: pytest.MonkeyPatch, content: bytes, parse: Any = None) -> Any:
    monkeypatch.setattr(
        st, "fetch_shuttle_pdf", AsyncMock(return_value=(PDF_PLACE, "u", content))
    )
    if parse is not None:
        monkeypatch.setattr(st, "parse_shuttle_pdf", parse)
    return asyncio.run(st.ingest_shuttle_pdf())


def _broken_parse(monkeypatch: pytest.MonkeyPatch) -> Any:
    title, trips, notes, _ = st.parse_shuttle_pdf(PDF.read_bytes())
    broken = [t for t in trips if t["day_type"] != "토요일"]
    return lambda _: (title, broken, notes, ["토요일"])


def test_approve_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch, PDF.read_bytes())
    _ingest(monkeypatch, b"%PDF v2", _broken_parse(monkeypatch))
    assert _public("2026-10-24")["source"]["needs_review"] is True
    summary = api.get(BASE, headers=ADMIN).json()[0]
    assert summary["needs_review"] and summary["pending"]["problems"]
    res = api.post(f"{BASE}/{PDF_PLACE}/review/approve", headers=ADMIN)
    assert res.status_code == 200, res.text
    assert res.json()["needs_review"] is False and res.json()["pending"] is None
    body = _public("2026-10-24")
    assert body["source"]["needs_review"] is False
    # 승격된 파싱본엔 토요일 편이 없음
    assert all(not d["segments"] for r in body["routes"] for d in r["directions"])
    assert (
        api.post(f"{BASE}/{PDF_PLACE}/review/approve", headers=ADMIN).status_code == 409
    )


def test_reject_pending_and_skip_same_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch, PDF.read_bytes())
    parse = _broken_parse(monkeypatch)
    _ingest(monkeypatch, b"%PDF v2", parse)
    res = api.post(f"{BASE}/{PDF_PLACE}/review/reject", headers=ADMIN)
    assert res.status_code == 200 and res.json()["needs_review"] is False
    assert _public("2026-10-24")["source"]["needs_review"] is False
    assert _ingest(monkeypatch, b"%PDF v2", parse) is None  # 거부한 PDF는 다시 안 함
    assert (
        api.post(f"{BASE}/{PDF_PLACE}/review/reject", headers=ADMIN).status_code == 409
    )


def test_first_failure_approve_and_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch, b"%PDF one", _broken_parse(monkeypatch))
    assert _public()["source"]["needs_review"] is True
    assert (
        api.post(f"{BASE}/{PDF_PLACE}/review/reject", headers=ADMIN).status_code == 409
    )
    assert (
        api.post(f"{BASE}/{PDF_PLACE}/review/approve", headers=ADMIN).status_code == 200
    )
    assert _public()["source"]["needs_review"] is False


def test_editing_pdf_doc_becomes_user_and_keeps_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ingest(monkeypatch, PDF.read_bytes())
    trip_id = api.get(f"{BASE}/{PDF_PLACE}", headers=ADMIN).json()["trips"][0]["id"]
    assert (
        api.delete(f"{BASE}/{PDF_PLACE}/trips/{trip_id}", headers=ADMIN).status_code
        == 204
    )
    doc = api.get(f"{BASE}/{PDF_PLACE}", headers=ADMIN).json()
    assert doc["source"] == "user" and doc["trip_count"] == 127
    assert (doc["valid_from"], doc["valid_to"]) == ("2026-09-01", "2026-12-22")
    assert _public()["source"]["origin"] == "user"
    # 수정본은 force 없는 동기화가 덮어쓰지 않음
    assert _ingest(monkeypatch, b"%PDF v3") is None


def test_approve_after_edit_needs_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """검토 대기 중 고친 내용이 승인으로 조용히 사라지지 않아야 합니다."""
    _ingest(monkeypatch, PDF.read_bytes())
    _ingest(monkeypatch, b"%PDF v2", _broken_parse(monkeypatch))
    add = api.post(
        f"{BASE}/{PDF_PLACE}/trips",
        json={"direction": "main_in", "day_type": "평일", "time": "07:00"},
        headers=ADMIN,
    )
    assert add.status_code == 201
    res = api.post(f"{BASE}/{PDF_PLACE}/review/approve", headers=ADMIN)
    assert res.status_code == 409 and "force=true" in res.json()["detail"]
    assert {"type": "fixed", "time": "07:00", "note": None} in _main_in(_public())
    # force 승인은 refresh force와 같이 전체 관리자만
    approve = f"{BASE}/{PDF_PLACE}/review/approve"
    assert api.post(approve, params={"force": "true"}, headers=ADMIN).status_code == 403
    forced = api.post(
        approve, params={"force": "true"}, headers=_auth(["global_admin"])
    )
    assert forced.status_code == 200 and forced.json()["source"] == "pdf"


@pytest.mark.parametrize("place", ["a/b", "a\nb", "a\x00", "x" * 101, "a\b"])
def test_place_rejected_422(place: str) -> None:
    res = api.post(BASE, json={**BODY, "place": place}, headers=ADMIN)
    assert res.status_code == 422


def test_audit_log_escapes_target(caplog: pytest.LogCaptureFixture) -> None:
    place = 'x" action=forged'
    assert (
        api.post(BASE, json={**BODY, "place": place}, headers=ADMIN).status_code == 201
    )
    line = next(
        r.getMessage()
        for r in caplog.records
        if "shuttle_admin.create" in r.getMessage()
    )
    assert line.endswith('target="x\\" action=forged"')


def test_if_match_conflict_412() -> None:
    created = api.post(BASE, json=BODY, headers=ADMIN).json()
    rev = created["revision"]
    trip_id = created["trips"][0]["id"]
    ok = api.patch(
        f"{BASE}/{PLACE}",
        json={"title": "1"},
        headers={**ADMIN, "If-Match": f'"{rev}"'},
    )
    assert ok.status_code == 200 and ok.json()["revision"] != rev
    stale = api.patch(
        f"{BASE}/{PLACE}",
        json={"title": "2"},
        headers={**ADMIN, "If-Match": f'"{rev}"'},
    )
    assert stale.status_code == 412
    put = api.put(
        f"{BASE}/{PLACE}/trips/{trip_id}",
        json=TRIP,
        headers={**ADMIN, "If-Match": rev},
    )
    assert put.status_code == 412
    # 헤더가 없으면 지금처럼 바로 저장
    assert (
        api.patch(f"{BASE}/{PLACE}", json={"title": "3"}, headers=ADMIN).status_code
        == 200
    )
    assert api.get(f"{BASE}/{PLACE}", headers=ADMIN).json()["title"] == "3"


def test_force_refresh_requires_global_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(bus, "sync_shuttle_timetables", sync)
    refresh = "/bus/timetable/refresh"
    assert api.post(refresh, params={"force": "true"}, headers=ADMIN).status_code == 403
    assert api.post(refresh, headers=ADMIN).status_code == 202
    sync.assert_called_once_with(False)
    gadmin = _auth(["global_admin"])
    assert (
        api.post(refresh, params={"force": "true"}, headers=gadmin).status_code == 202
    )


def test_auto_update_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(monkeypatch, PDF.read_bytes())
    assert api.get(f"{BASE}/{PDF_PLACE}", headers=ADMIN).json()["auto_update"] is True
    api.patch(f"{BASE}/{PDF_PLACE}", json={"title": "고침"}, headers=ADMIN)
    listed = api.get(BASE, headers=ADMIN).json()[0]
    assert listed["auto_update"] is False and listed["source"] == "user"


def _store_raw(doc: dict) -> None:
    Path(Config.SHUTTLE_TIMETABLE_PATH).write_text(
        json.dumps({doc["place"]: doc}, ensure_ascii=False), encoding="utf-8"
    )


def test_legacy_bad_trips_visible_and_fixable() -> None:
    """입력 규칙에 어긋난 옛 편도 관리자 상세엔 원시값+problems로 보이고 고칠 수 있음."""
    base = {
        "direction": "정왕역 → 본교",
        "day_type": "평일",
        "day_note": None,
        "valid_from": None,
        "valid_to": None,
        "period_note": None,
        "boarding_place": None,
        "note": None,
        "route": "정왕역 ↔ 본교",
        "origin": "정왕역",
        "via": [],
        "destination": "본교",
    }
    trips = [
        {**base, "departure_time": "08:00", "departure_time_end": None},
        {**base, "departure_time": "09:00", "departure_time_end": "09:30"},  # fixed+end
        {
            **base,
            "departure_time": "18:00",
            "departure_time_end": "17:00",
            "kind": "frequent",
        },
        {**base, "departure_time": "25:99", "departure_time_end": None},
    ]
    trips[1]["kind"] = "fixed"
    _store_raw(
        {"place": PLACE, "source": "user", "ref_date": "2026-09-01", "trips": trips}
    )
    res = api.get(f"{BASE}/{PLACE}", headers=ADMIN)
    assert res.status_code == 200, res.text
    got = res.json()["trips"]
    assert len(got) == 4
    assert got[0]["problems"] == []
    assert all(t["problems"] for t in got[1:])
    assert got[3]["time"] == "25:99"
    # 공개 GET은 해석 불가 편만 건너뛰고 200
    assert _public()["routes"][0]["directions"][1]["segments"]
    # 문제 편을 고치고 지울 수 있음
    fixed = api.put(f"{BASE}/{PLACE}/trips/{got[3]['id']}", json=TRIP, headers=ADMIN)
    assert fixed.status_code == 200 and fixed.json()["problems"] == []
    assert (
        api.delete(f"{BASE}/{PLACE}/trips/{got[2]['id']}", headers=ADMIN).status_code
        == 204
    )
    assert len(api.get(f"{BASE}/{PLACE}", headers=ADMIN).json()["trips"]) == 3


def test_always_active_flag() -> None:
    api.post(BASE, json={**BODY, "valid_from": None, "valid_to": None}, headers=ADMIN)
    assert api.get(f"{BASE}/{PLACE}", headers=ADMIN).json()["always_active"] is True
    api.patch(
        f"{BASE}/{PLACE}",
        json={"valid_from": "2026-09-01", "valid_to": "2026-12-22"},
        headers=ADMIN,
    )
    assert api.get(BASE, headers=ADMIN).json()[0]["always_active"] is False


def test_if_match_on_trip_post_delete_and_doc_delete() -> None:
    created = api.post(BASE, json=BODY, headers=ADMIN).json()
    rev, trip_id = created["revision"], created["trips"][0]["id"]
    stale = {**ADMIN, "If-Match": '"0000000000000000"'}
    assert (
        api.post(f"{BASE}/{PLACE}/trips", json=TRIP, headers=stale).status_code == 412
    )
    assert (
        api.delete(f"{BASE}/{PLACE}/trips/{trip_id}", headers=stale).status_code == 412
    )
    assert api.delete(f"{BASE}/{PLACE}", headers=stale).status_code == 412
    star = {**ADMIN, "If-Match": "*"}
    assert api.post(f"{BASE}/{PLACE}/trips", json=TRIP, headers=star).status_code == 201
    assert (
        api.post(f"{BASE}/없는문서/trips", json=TRIP, headers=star).status_code == 404
    )
    rev = api.get(f"{BASE}/{PLACE}", headers=ADMIN).json()["revision"]
    ok = {**ADMIN, "If-Match": f'"{rev}"'}
    assert api.delete(f"{BASE}/{PLACE}/trips/{trip_id}", headers=ok).status_code == 204
    rev = api.get(f"{BASE}/{PLACE}", headers=ADMIN).json()["revision"]
    assert (
        api.delete(f"{BASE}/{PLACE}", headers={**ADMIN, "If-Match": rev}).status_code
        == 204
    )
