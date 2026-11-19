"""실제 PDF 고정 자료로 파서 회귀를 막습니다.

자료: tests/fixtures/shuttle_2026_2nd.pdf — iBook에 올라온
"26년 셔틀버스 시간표 (2학기) 9.1 ~ 12.22" 원본(학교 공개 자료, 파서 회귀 테스트용).
"""

from collections import defaultdict
from pathlib import Path

from app.schemas.shuttle import ParsedTrip
from app.utils.shuttle_pdf import parse_shuttle_pdf

PDF = Path(__file__).parent / "fixtures" / "shuttle_2026_2nd.pdf"


def _by_direction():
    title, trips, notes, days = parse_shuttle_pdf(PDF.read_bytes())
    assert days == ["토요일", "평일"]
    assert notes == [
        "소요시간 10분, 퇴근(혼잡) 시간대는 20~30분 소요 예상",
        "혼잡할 시 외부인은 탑승이 제한될 수 있습니다. (음료수를 들고 타지 마세요)",
    ]
    groups = defaultdict(list)
    for t in trips:
        ParsedTrip(**t)  # 저장 모델로 그대로 검증되는 모양이어야 함
        groups[(t["direction"], t["day_type"])].append(t)
    for v in groups.values():
        v.sort(key=lambda t: t["departure_time"])
    return title, trips, groups


def test_fixture_directions_and_counts():
    title, trips, g = _by_direction()
    assert title == "◈ 2학기 셔틀버스 시간표 ◈"
    assert {t["valid_from"] for t in trips} == {"09-01"}
    assert {t["valid_to"] for t in trips} == {"12-22"}
    counts = {k: len(v) for k, v in g.items()}
    assert counts == {
        ("본교 → 정왕역", "평일"): 56,
        ("정왕역 → 본교", "평일"): 36,
        ("본교 → 제2캠퍼스", "평일"): 10,
        ("제2캠퍼스 → 본교", "평일"): 11,
        ("정왕역 → 본교 → 제2캠퍼스", "평일"): 2,
        ("정왕역 → 본교 → 제2캠퍼스", "토요일"): 6,
        ("제2캠퍼스 → 본교 → 정왕역", "토요일"): 7,
    }


def test_fixture_main_route_segments():
    _, _, g = _by_direction()
    inbound = g[("정왕역 → 본교", "평일")]
    first, last = inbound[0], inbound[-1]
    assert (first["kind"], first["departure_time"], first["departure_time_end"]) == (
        "frequent",
        "08:40",
        "10:00",
    )
    assert first["note"] == "수시운행"
    # '※ 17시 이후 시간은 ★ 도착버스 탑승' + '22 ~ 17분 (막차)'는 막차 1편이 아니라 구간
    board = [t for t in inbound if t["kind"] == "arrival_board"]
    assert len(board) == 1 and board[0] is last
    assert (last["departure_time"], last["departure_time_end"]) == ("17:00", "22:17")
    assert last["note"] == "도착버스 탑승"
    assert not any(t["note"] == "막차" for t in inbound)
    fixed = [t["departure_time"] for t in inbound if t["kind"] == "fixed"]
    assert fixed[0] == "10:00" and fixed[-1] == "16:50" and len(fixed) == 34

    outbound = g[("본교 → 정왕역", "평일")]
    assert outbound[0]["departure_time"] == "09:00"
    assert outbound[-1]["departure_time"] == "22:40"
    frequent = [t for t in outbound if t["kind"] == "frequent"]
    assert [(t["departure_time"], t["departure_time_end"]) for t in frequent] == [
        ("17:00", "18:00")
    ]


def test_fixture_campus2_columns():
    _, _, g = _by_direction()

    def times(direction, day):
        return [t["departure_time"] for t in g[(direction, day)]]

    assert times("본교 → 제2캠퍼스", "평일") == [
        "10:00", "11:00", "12:00", "13:00", "14:00",
        "15:00", "16:00", "17:00", "18:00", "19:00",
    ]  # fmt: skip
    inbound = g[("제2캠퍼스 → 본교", "평일")]
    assert inbound[0]["departure_time"] == "09:40"
    assert inbound[-1]["departure_time"] == "19:30"
    # '(오이도역 도착)'은 운행편이 아니라 17:40편의 비고
    assert [t["note"] for t in inbound if t["note"]] == ["오이도역 도착"]
    assert [t["departure_time"] for t in inbound if t["note"]] == ["17:40"]
    # '(정왕역출발 8:55, 9시 )'는 본교 출발편이 아니라 정왕역 출발 경유편
    assert times("정왕역 → 본교 → 제2캠퍼스", "평일") == ["08:55", "09:00"]
    assert times("정왕역 → 본교 → 제2캠퍼스", "토요일") == [
        "08:45", "08:50", "09:00", "09:05", "09:10", "09:15",
    ]  # fmt: skip
    assert times("제2캠퍼스 → 본교 → 정왕역", "토요일") == [
        "16:30", "16:45", "19:25", "19:28", "19:30", "19:35", "19:45",
    ]  # fmt: skip
    saturday = g[("제2캠퍼스 → 본교 → 정왕역", "토요일")]
    assert {t["day_note"] for t in saturday} == {"일학습병행학부"}
    assert all(t["kind"] == "fixed" for t in saturday)


def test_parsed_trip_kind_default():
    """kind가 없던 기존 입력(저장본·PUT 본문)도 받아야 합니다."""
    base = {
        "direction": "정왕역 → 본교",
        "day_type": "평일",
        "day_note": None,
        "departure_time": "08:40",
        "valid_from": None,
        "valid_to": None,
        "period_note": None,
        "boarding_place": None,
        "note": None,
    }
    assert ParsedTrip(**base, departure_time_end=None).kind == "fixed"
    assert ParsedTrip(**base, departure_time_end="10:00").kind == "frequent"
    assert (
        ParsedTrip(**base, departure_time_end="22:17", kind="arrival_board").kind
        == "arrival_board"
    )
