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
    # 장소가 하나뿐인 조각은 방향으로 확정하지 않는다
    assert _canon_direction("본교↔") is None
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
        patch.object(st, "parse_shuttle_pdf", return_value=("제목", PARSED)) as p,
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
    asyncio.run(st.save_user_timetable("사용자수정", [parsed_trip]))
    store = st.load_store()
    assert store["사용자수정"]["source"] == "user"

    with (
        patch.object(st, "fetch_shuttle_pdf", _fetch(place="사용자수정")),
        patch.object(st, "parse_shuttle_pdf", return_value=("제목", PARSED)) as p,
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


def check_date_filter_and_dedupe():
    """query_trips의 날짜 필터 + (기간·방향·운행일·시각) 기준 dedupe."""
    always = trip("본교 → 정왕역", "평일", "07:00")  # 기간 표기 없음 → 상시
    summer_normal = trip(
        "본교 → 정왕역", "평일", "08:00",
        valid_from="08-25", valid_to="08-31", period_note="정상근무",
    )
    summer_short = trip(
        "본교 → 정왕역", "평일", "08:00",
        valid_from="07-01", valid_to="07-13", period_note="단축근무",
    )
    winter_break = trip(
        "본교 → 정왕역", "평일", "23:00",
        valid_from="12-20", valid_to="02-28", period_note="겨울방학",
    )
    summer_normal_dup = summer_normal.model_copy(update={"period_note": "정상근무(통합)"})

    store = {
        "개별표": {"trips": [t.model_dump() for t in [always, summer_normal, winter_break]]},
        "통합표": {"trips": [t.model_dump() for t in [summer_normal_dup, summer_short]]},
    }

    in_range = st.query_trips(store, on_date=date(2026, 8, 27))
    times = {t["departure_time"] for t in in_range}
    assert "08:00" in times and "07:00" in times
    assert "23:00" not in times

    matches = [t for t in in_range if t["departure_time"] == "08:00"]
    assert len(matches) == 1
    assert matches[0]["place"] == "개별표"
    assert matches[0]["period_note"] == "정상근무"

    short_range = st.query_trips(store, on_date=date(2026, 7, 5))
    assert any(
        t["departure_time"] == "08:00" and t["period_note"] == "단축근무" for t in short_range
    )
    assert not any(t["period_note"] == "정상근무" for t in short_range)

    out_of_range = st.query_trips(store, on_date=date(2026, 9, 10))
    assert {t["departure_time"] for t in out_of_range} == {"07:00"}

    jan = st.query_trips(store, on_date=date(2026, 1, 15))
    assert "23:00" in {t["departure_time"] for t in jan}

    all_trips = st.query_trips(store, include_all=True)
    assert {t["departure_time"] for t in all_trips} == {"07:00", "08:00", "23:00"}
    assert len(all_trips) == 4


def run():
    check_direction_enum()
    check_parser_helpers()
    check_fetch_pdf()
    check_hash_guard()
    check_user_override()
    check_sync()
    check_date_filter_and_dedupe()

    # 저장 → 조회 전 구간
    with (
        patch.object(st, "fetch_shuttle_pdf", _fetch()),
        patch.object(st, "parse_shuttle_pdf", return_value=("2학기 셔틀버스 시간표", PARSED)),
    ):
        doc = asyncio.run(st.ingest_shuttle_pdf())
    assert len(doc["trips"]) == 2
    assert os.path.exists(st.Config.SHUTTLE_TIMETABLE_PATH)
    assert any(f.endswith(".pdf") for f in os.listdir(st.Config.SHUTTLE_PDF_DIR))

    store = st.load_store()
    assert len(st.query_trips(store, day_type="평일", on_date=date(2026, 9, 10))) == 1
    # 기간(09-01~12-22) 밖 날짜면 빠진다
    assert st.query_trips(store, on_date=date(2026, 1, 5)) == []

    print("OK")


if __name__ == "__main__":
    run()
