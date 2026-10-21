"""셔틀 시간표 파이프라인 자체 점검. 실행: python tests/test_shuttle_timetable.py

PDF 다운로드와 파싱은 모킹합니다. 파서 자체의 추출 정확도는 실제 PDF 69건
(2024년 이후 iBook 등록분) 전수 검증으로 확인했으며, 여기서는 파이프라인
(해시 가드, 사용자 수정본 우선, 저장/조회, 날짜 필터)만 봅니다.
"""

import asyncio
import os
import sys
import tempfile
from datetime import date
from typing import get_args
from unittest.mock import AsyncMock, patch

os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.shuttle_routes import SHUTTLE_DIRECTIONS  # noqa: E402
from app.schemas.shuttle import Direction, ParsedTrip, ShuttleTrip  # noqa: E402
from app.utils import shuttle_timetable as st  # noqa: E402
from app.utils.shuttle_pdf import _canon_direction, _day_type, _period  # noqa: E402


def trip(direction, day_type, t, end=None, **kw):
    fields = {
        "day_note": None,
        "boarding_place": None,
        "note": None,
        "valid_from": None,
        "valid_to": None,
        "period_note": None,
    } | kw
    return ShuttleTrip(
        direction=direction,
        day_type=day_type,
        departure_time=t,
        departure_time_end=end,
        **fields,
        **SHUTTLE_DIRECTIONS[direction],
    )


# parse_shuttle_pdf 가 돌려주는 모양 (ParsedTrip 필드를 가진 dict)
PARSED = [
    {
        "direction": "정왕역 → 본교",
        "day_type": "평일",
        "day_note": None,
        "departure_time": "08:40",
        "departure_time_end": "10:00",
        "valid_from": "09-01",
        "valid_to": "12-22",
        "period_note": None,
        "boarding_place": None,
        "note": "수시운행",
    },
    {
        "direction": "제2캠퍼스 → 본교 → 정왕역",
        "day_type": "토요일",
        "day_note": None,
        "departure_time": "16:30",
        "departure_time_end": None,
        "valid_from": "09-01",
        "valid_to": "12-22",
        "period_note": None,
        "boarding_place": None,
        "note": None,
    },
]

PDF_BYTES = b"%PDF-1.4 fake"


def _fetch(content=PDF_BYTES, place="26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22"):
    return AsyncMock(return_value=(place, "https://x/shuttle.pdf", content))


def check_direction_enum():
    """방향 enum과 상수 표가 같은 집합이어야 합니다."""
    assert set(get_args(Direction)) == set(SHUTTLE_DIRECTIONS)


def check_parser_helpers():
    """파서의 정규화 헬퍼. 좌표 파싱은 실제 PDF로 별도 검증했습니다."""
    # 긴 이름 우선 매칭이라 '제2캠퍼스'가 '2캠'으로 잘리지 않는다
    assert _canon_direction("본교 ⇒ 2캠퍼스") == "본교 → 제2캠퍼스"
    assert _canon_direction("정왕역⇒본교⇒2캠퍼스") == "정왕역 → 본교 → 제2캠퍼스"
    # 정왕역↔2캠은 직행이 없어 본교 경유를 채워 넣는다 (상수 표에 직행이 없음)
    assert _canon_direction("정왕역 ↔ 제2캠퍼스") == "정왕역 → 본교 → 제2캠퍼스"
    assert _canon_direction("제2캠퍼스 출발") is None  # 장소 하나뿐이면 확정하지 않음
    # 장소가 하나뿐인 조각은 방향으로 확정하지 않는다
    assert _canon_direction("본교↔") is None
    # 확정된 방향은 전부 상수 표에 있어야 한다
    for h in ("본교 ⇒ 2캠퍼스", "정왕역⇒본교⇒2캠퍼스", "정왕역 ↔ 제2캠퍼스", "2캠퍼스⇒본교⇒정왕역"):
        assert _canon_direction(h) in SHUTTLE_DIRECTIONS
    assert _day_type("『정왕역 ↔ 한국공대 본교』시간표 (평일)") == "평일"
    # 제목에 운행일이 없으면 본문에서 찾는다
    assert _day_type("셔틀버스 안내\n토요일 운행") == "토요일"
    assert _period("소요시간 10분 9.1 ~ 12.22") == ("09-01", "12-22")
    # 시각(HH:MM)은 기간으로 오인하지 않는다
    assert _period("17:00 ~ 18:00 수시운행") == (None, None)


def check_hash_guard():
    """같은 PDF로 두 번 ingest하면 두 번째는 파싱하지 않고 None."""
    parse = AsyncMock()
    with (
        patch.object(st, "fetch_shuttle_pdf", _fetch(place="해시가드")),
        patch.object(st, "parse_shuttle_pdf", return_value=("제목", PARSED, ["안내"])) as p,
    ):
        doc1 = asyncio.run(st.ingest_shuttle_pdf())
        doc2 = asyncio.run(st.ingest_shuttle_pdf())
        assert p.call_count == 1  # 두 번째는 해시가 같아 파싱 안 함
        doc3 = asyncio.run(st.ingest_shuttle_pdf(force=True))
        assert p.call_count == 2  # force면 해시 무시

    assert doc1 is not None and doc2 is None and doc3 is not None
    assert doc1["pdf_sha256"] and doc1["source"] == "pdf" and doc1["title"] == "제목"
    assert len(doc1["trips"]) == 2
    # 방향 상수에서 노선 정보가 채워졌는지
    assert doc1["trips"][0]["route"] and doc1["trips"][0]["origin"] == "정왕역"
    assert parse.await_count == 0
    asyncio.run(st.delete_timetable("해시가드"))


def check_user_override():
    """사용자 수정본은 ingest가 건드리지 않고, force=True일 때만 덮어씁니다."""
    parsed_trip = ParsedTrip(**PARSED[0])
    asyncio.run(
        st.save_user_timetable("사용자수정", [parsed_trip], "직접 제목", updated_by="sub-1")
    )
    store = st.load_store()
    assert store["사용자수정"]["source"] == "user"
    # PUT 본문 title이 버려지던 버그(F7) 회귀 방지
    assert store["사용자수정"]["title"] == "직접 제목"
    assert store["사용자수정"]["updated_by"] == "sub-1"

    with (
        patch.object(st, "fetch_shuttle_pdf", _fetch(place="사용자수정")),
        patch.object(st, "parse_shuttle_pdf", return_value=("제목", PARSED, ["안내"])) as p,
    ):
        assert asyncio.run(st.ingest_shuttle_pdf()) is None
        assert p.call_count == 0
        doc = asyncio.run(st.ingest_shuttle_pdf(force=True))
        assert doc is not None and doc["source"] == "pdf" and p.call_count == 1

    assert asyncio.run(st.delete_timetable("사용자수정")) is True
    assert "사용자수정" not in st.load_store()
    assert asyncio.run(st.delete_timetable("사용자수정")) is False


def check_sync():
    """sync는 갱신된 place를 돌려주고, 예외는 삼켜서 빈 목록을 돌려줍니다."""
    with patch.object(st, "ingest_shuttle_pdf", AsyncMock(return_value={"place": "A"})):
        assert asyncio.run(st.sync_shuttle_timetables()) == ["A"]
    with patch.object(st, "ingest_shuttle_pdf", AsyncMock(return_value=None)):
        assert asyncio.run(st.sync_shuttle_timetables()) == []
    with patch.object(st, "ingest_shuttle_pdf", AsyncMock(side_effect=RuntimeError("boom"))):
        assert asyncio.run(st.sync_shuttle_timetables()) == []


def check_fetch_pdf():
    """RawFileList → 파일 URL → 다운로드. place는 파일명에서 확장자를 뗀 값."""

    class _Resp:
        content = PDF_BYTES

        def raise_for_status(self):
            pass

    async def fake_file_list(self):
        self.file_name = "26년 셔틀버스 시간표 (2학기).pdf"
        return "<rawfiles/>"

    with (
        patch("app.utils.shuttle_timetable.BookDownloader.fetch_file_list", fake_file_list),
        patch(
            "app.utils.shuttle_timetable.BookDownloader.get_file_url",
            lambda self, _: "https://x/a.pdf",
        ),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_Resp())),
    ):
        place, url, content = asyncio.run(st.fetch_shuttle_pdf())
    assert place == "26년 셔틀버스 시간표 (2학기)"
    assert url == "https://x/a.pdf" and content == PDF_BYTES


def check_build_timetable():
    """날짜 필터·운행일 매핑·연도 계산·문서 선택·version."""
    always = trip("본교 → 정왕역", "평일", "07:00")  # 기간 표기 없음 → 상시
    fall = trip("본교 → 정왕역", "평일", "08:00", valid_from="09-01", valid_to="12-22")
    weekend = trip("정왕역 → 본교", "주말", "09:00", valid_from="09-01", valid_to="12-22")
    holiday = trip("정왕역 → 본교", "공휴일", "10:00", valid_from="09-01", valid_to="12-22")
    winter = trip("본교 → 정왕역", "평일", "23:00", valid_from="12-20", valid_to="02-28")
    doc = {
        "place": "2학기",
        "source": "pdf",
        "trips": [t.model_dump() for t in [always, fall, fall, weekend, holiday]],
    }

    def seg(res, dir_id):
        return [
            d["segments"]
            for r in res["routes"]
            for d in r["directions"]
            if d["id"] == dir_id
        ][0]

    thu = st.build_timetable(doc, date(2026, 10, 22))
    assert thu["day_type"] == "weekday" and thu["holiday_name"] is None
    assert (thu["valid_from"], thu["valid_to"]) == ("2026-09-01", "2026-12-22")
    assert [d["id"] for r in thu["routes"] for d in r["directions"]] == [
        "main_out", "main_in", "campus2_out",
        "campus2_in", "campus2_from_station", "campus2_to_station",
    ]  # fmt: skip
    # 중복 제거 + 오름차순 + 운행 없는 방향도 빈 목록
    assert seg(thu, "main_out") == [
        {"type": "fixed", "time": "07:00", "note": None},
        {"type": "fixed", "time": "08:00", "note": None},
    ]
    assert seg(thu, "main_in") == [] and seg(thu, "campus2_to_station") == []

    sat = st.build_timetable(doc, date(2026, 10, 24))  # '주말' 표는 토·일 양쪽
    assert sat["day_type"] == "saturday" and seg(sat, "main_in")[0]["time"] == "09:00"
    sun = st.build_timetable(doc, date(2026, 10, 25))
    assert sun["day_type"] == "sunday" and seg(sun, "main_in")[0]["time"] == "09:00"
    hol = st.build_timetable(doc, date(2026, 10, 9))  # 한글날(금)
    assert (hol["day_type"], hol["holiday_name"]) == ("holiday", "한글날")
    assert [g["time"] for g in seg(hol, "main_in")] == ["10:00"]
    assert seg(hol, "main_out") == []

    # 기간 밖이면 비지만 기간 표기는 그대로(연도는 조회일 기준)
    jan = st.build_timetable(doc, date(2027, 1, 5))
    assert [g["time"] for g in seg(jan, "main_out")] == ["07:00"]
    assert (jan["valid_from"], jan["valid_to"]) == ("2027-09-01", "2027-12-22")

    # 연말 걸침: 1월이면 전년도 12월부터, 12월이면 다음 해 2월까지
    wdoc = {"place": "겨울", "source": "pdf", "trips": [winter.model_dump()]}
    w1 = st.build_timetable(wdoc, date(2027, 1, 15))
    assert (w1["valid_from"], w1["valid_to"]) == ("2026-12-20", "2027-02-28")
    w2 = st.build_timetable(wdoc, date(2026, 12, 21))
    assert (w2["valid_from"], w2["valid_to"]) == ("2026-12-20", "2027-02-28")
    assert seg(w2, "main_out")[0]["time"] == "23:00"

    # version: 날짜와 무관, 내용·needs_review가 바뀌면 바뀜
    assert thu["version"] == sat["version"] == jan["version"]
    assert len(thu["version"]) == 16
    reviewing = st.build_timetable(doc, date(2026, 10, 22), needs_review=True)
    assert reviewing["version"] != thu["version"]
    changed = {**doc, "notes": ["새 안내"]}
    assert st.build_timetable(changed, date(2026, 10, 22))["version"] != thu["version"]

    # 문서 선택: 조회일에 운행하는 문서, 그중 사용자 수정본 우선
    old = {"place": "1학기", "source": "pdf", "parsed_at": "2026-03-01", "trips": [
        trip("본교 → 정왕역", "평일", "06:00", valid_from="03-02", valid_to="06-20")
        .model_dump()
    ]}  # fmt: skip
    store = {"1학기": old, "2학기": {**doc, "parsed_at": "2026-09-01"}}
    assert st.pick_document(store, date(2026, 10, 22))["place"] == "2학기"
    assert st.pick_document(store, date(2026, 4, 1))["place"] == "2학기"  # 상시 trip
    store["직접"] = {**doc, "place": "직접", "source": "user"}
    assert st.pick_document(store, date(2026, 10, 22))["place"] == "직접"


def run():
    check_direction_enum()
    check_parser_helpers()
    check_fetch_pdf()
    check_hash_guard()
    check_user_override()
    check_sync()
    check_build_timetable()

    # 저장 → 조회 전 구간
    with (
        patch.object(st, "fetch_shuttle_pdf", _fetch()),
        patch.object(st, "parse_shuttle_pdf", return_value=("2학기", PARSED, [])),
    ):
        doc = asyncio.run(st.ingest_shuttle_pdf())
    assert len(doc["trips"]) == 2
    assert os.path.exists(st.Config.SHUTTLE_TIMETABLE_PATH)
    assert any(f.endswith(".pdf") for f in os.listdir(st.Config.SHUTTLE_PDF_DIR))

    store = st.load_store()
    day = date(2026, 9, 10)
    res = st.build_timetable(st.pick_document(store, day), day)
    main_in = res["routes"][0]["directions"][1]
    assert main_in["segments"] == [
        {"type": "frequent", "start": "08:40", "end": "10:00", "note": "수시운행"}
    ]

    print("OK")


def test_pipeline(tmp_path, monkeypatch):
    """pytest 진입점. 다른 테스트가 Config를 먼저 import해도 임시 경로를 쓰게 고정합니다."""
    monkeypatch.setattr(st.Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        st.Config, "SHUTTLE_TIMETABLE_PATH", str(tmp_path / "shuttle_timetable.json")
    )
    monkeypatch.setattr(st.Config, "SHUTTLE_PDF_DIR", str(tmp_path / "shuttle_pdf"))
    run()


if __name__ == "__main__":
    run()
