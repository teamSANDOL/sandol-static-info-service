"""셔틀 시간표 파이프라인 자체 점검. 실행: python tests/test_shuttle_timetable.py

OpenAI/HTTP는 모킹. 실제 AI 파싱 품질은 POST /bus/timetable/refresh 로 확인.
"""

import asyncio
import hashlib
import os
import sys
import tempfile
from datetime import date
from unittest.mock import AsyncMock, patch

TEST_API_KEY = "test-api-key"
os.environ["STATIC_INFO_API_KEY_SHA256"] = hashlib.sha256(TEST_API_KEY.encode()).hexdigest()
os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.schemas.shuttle import ParsedTrip, ShuttleTimetable, ShuttleTrip  # noqa: E402
from app.utils import shuttle_timetable as st  # noqa: E402

from typing import get_args  # noqa: E402

from app.config.shuttle_routes import SHUTTLE_DIRECTIONS  # noqa: E402
from app.schemas.shuttle import Direction  # noqa: E402


def trip(direction, day_type, t, end=None, **kw):
    fields = {
        "day_note": None, "boarding_place": None, "note": None,
        "valid_from": None, "valid_to": None, "period_note": None,
    } | kw
    return ShuttleTrip(
        direction=direction, day_type=day_type, departure_time=t, departure_time_end=end,
        **fields, **SHUTTLE_DIRECTIONS[direction],
    )


FAKE = ShuttleTimetable(
    trips=[
        trip("정왕역 → 본교", "평일", "08:40", "10:00", note="수시운행"),
        trip("제2캠퍼스 → 본교 → 정왕역", "토요일", "16:30", day_note="일학습병행학부",
             boarding_place="본교 운동장 옆(서문) 버스정류장"),
    ]
)


class _Resp:
    content = b"\xff\xd8fake-jpeg"
    headers = {"content-type": "image/jpeg"}

    def raise_for_status(self):
        pass


def _variant(*drop, extra=None):
    trips = [t for i, t in enumerate(FAKE.trips) if i not in drop]
    if extra:
        trips.append(FAKE.trips[0].model_copy(update={"departure_time": extra}))
    return ShuttleTimetable(trips=trips)


def check_consensus():
    # 3개 일치 → 추가 호출 없이 채택
    parse = AsyncMock(return_value=FAKE)
    with patch.object(st, "parse_shuttle_image", parse):
        tt, votes = asyncio.run(st.parse_shuttle_image_consensus(b"", "image/jpeg"))
    assert parse.await_count == 3 and votes == {"runs": 3, "unanimous": True, "agreement": 1.0}

    # 불일치 → 2회 추가, 5개 중 다수(FAKE 3개)와 가장 잘 맞는 결과 채택
    seq = [FAKE, _variant(1), _variant(extra="23:59"), FAKE, FAKE]
    parse = AsyncMock(side_effect=seq)
    with patch.object(st, "parse_shuttle_image", parse):
        tt, votes = asyncio.run(st.parse_shuttle_image_consensus(b"", "image/jpeg"))
    assert parse.await_count == 5 and votes["runs"] == 5 and not votes["unanimous"]
    assert st.trip_keys(tt) == st.trip_keys(FAKE)
    assert 0 < votes["agreement"] < 1

    # 비고 차이는 무시, 시각 차이는 감지
    a = FAKE.trips[0]
    b = a.model_copy(update={"note": "다름", "boarding_place": "x"})
    assert st.trip_keys(ShuttleTimetable(trips=[a])) == st.trip_keys(ShuttleTimetable(trips=[b]))
    assert st.similarity(st.trip_keys(FAKE), st.trip_keys(_variant(1))) == 0.5

    # 운행 기간이 다르면 다른 trip으로 취급 (period_note는 무시)
    c = a.model_copy(update={"valid_from": "08-25", "valid_to": "08-31"})
    d = c.model_copy(update={"period_note": "정상근무"})
    assert st.trip_keys(ShuttleTimetable(trips=[a])) != st.trip_keys(ShuttleTimetable(trips=[c]))
    assert st.trip_keys(ShuttleTimetable(trips=[c])) == st.trip_keys(ShuttleTimetable(trips=[d]))

    # 방향 enum과 상수 표가 같은 집합
    assert set(get_args(Direction)) == set(SHUTTLE_DIRECTIONS)


def check_hash_guard():
    """같은 이미지로 두 번 ingest하면 두 번째는 파싱하지 않고 None."""
    parse = AsyncMock(return_value=FAKE)
    with (
        patch.object(st, "parse_shuttle_image", parse),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_Resp())),
    ):
        doc1 = asyncio.run(st.ingest_shuttle_image("해시가드", "http://x/hash.jpg", 1))
        doc2 = asyncio.run(st.ingest_shuttle_image("해시가드", "http://x/hash.jpg", 1))
    assert doc1 is not None and parse.await_count == 3  # consensus 3회
    assert doc2 is None and parse.await_count == 3  # 추가 호출 없음
    assert doc1["image_sha256"] and doc1["source"] == "ai" and doc1["page"] == 1

    # force=True면 해시가 같아도 다시 파싱
    with (
        patch.object(st, "parse_shuttle_image", parse),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_Resp())),
    ):
        doc3 = asyncio.run(
            st.ingest_shuttle_image("해시가드", "http://x/hash.jpg", 1, force=True)
        )
    assert doc3 is not None and parse.await_count == 6
    asyncio.run(st.delete_timetable("해시가드"))  # 이후 검증에 영향 없도록 정리


def check_user_override():
    """사용자 수정본은 ingest가 건드리지 않고, force=True일 때만 AI가 덮어씀."""
    parsed_trip = ParsedTrip(**FAKE.trips[0].model_dump(include=set(ParsedTrip.model_fields)))
    asyncio.run(st.save_user_timetable("사용자수정", [parsed_trip]))
    store = st.load_store()
    assert store["사용자수정"]["source"] == "user"
    assert "model" not in store["사용자수정"] and "votes" not in store["사용자수정"]

    parse = AsyncMock(return_value=FAKE)
    with (
        patch.object(st, "parse_shuttle_image", parse),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_Resp())),
    ):
        doc = asyncio.run(st.ingest_shuttle_image("사용자수정", "http://x/user.jpg", 1))
        assert doc is None and parse.await_count == 0  # 다운로드/파싱 모두 건너뜀

        doc = asyncio.run(
            st.ingest_shuttle_image("사용자수정", "http://x/user.jpg", 1, force=True)
        )
        assert doc is not None and doc["source"] == "ai" and parse.await_count == 3

    # 되돌리기: 삭제 후 재조회하면 없음
    assert asyncio.run(st.delete_timetable("사용자수정")) is True
    assert "사용자수정" not in st.load_store()
    assert asyncio.run(st.delete_timetable("사용자수정")) is False


def check_sync():
    """sync_shuttle_timetables는 페이지 번호로 place("{idx}페이지")를 만들어 ingest합니다.

    방학엔 페이지 수/내용이 유동적(안내도, 기간별 개별표, 통합표)이라 페이지→노선 고정 매핑을 두지
    않고, 항상 출처 페이지 번호를 place로 씁니다.
    """
    calls = []

    async def fake_ingest(place, url, page, force=False):
        calls.append((place, url, page, force))
        return {"place": place}

    with (
        patch(
            "app.utils.shuttle_timetable.BookDownloader.fetch_image_list",
            AsyncMock(return_value=["http://x/1.jpg", "http://x/2.jpg", "http://x/3.jpg"]),
        ),
        patch.object(st, "ingest_shuttle_image", AsyncMock(side_effect=fake_ingest)),
    ):
        updated = asyncio.run(st.sync_shuttle_timetables())
    assert calls == [
        ("1페이지", "http://x/1.jpg", 1, False),
        ("2페이지", "http://x/2.jpg", 2, False),
        ("3페이지", "http://x/3.jpg", 3, False),
    ]
    assert updated == ["1페이지", "2페이지", "3페이지"]

    # place별 예외는 잡아서 계속 진행
    async def flaky_ingest(place, url, page, force=False):
        if place == "1페이지":
            raise RuntimeError("boom")
        return {"place": place}

    with (
        patch(
            "app.utils.shuttle_timetable.BookDownloader.fetch_image_list",
            AsyncMock(return_value=["http://x/1.jpg", "http://x/2.jpg"]),
        ),
        patch.object(st, "ingest_shuttle_image", AsyncMock(side_effect=flaky_ingest)),
    ):
        updated = asyncio.run(st.sync_shuttle_timetables())
    assert updated == ["2페이지"]


def check_date_filter_and_dedupe():
    """query_trips의 날짜 필터 + (기간·방향·운행일·시각) 기준 dedupe를 점검합니다.

    store 순서(page 오름차순)상 개별 기간표가 통합표보다 앞선다고 가정하고,
    먼저 나온 trip을 유지합니다.
    """
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
    # 통합표 쪽 중복(같은 기간·방향·운행일·시각) — period_note만 다름
    summer_normal_dup = summer_normal.model_copy(
        update={"period_note": "정상근무(통합)"}
    )

    store = {
        "2페이지": {"trips": [t.model_dump() for t in [always, summer_normal, winter_break]]},
        "6페이지": {"trips": [t.model_dump() for t in [summer_normal_dup, summer_short]]},
    }

    # 기간 안 날짜: summer_normal만 포함 (summer_short는 기간 밖)
    in_range = st.query_trips(store, on_date=date(2026, 8, 27))
    times = {t["departure_time"] for t in in_range}
    assert "08:00" in times and "07:00" in times  # 상시 trip은 항상 포함
    assert "23:00" not in times  # 겨울방학 기간 아님

    # 통합표와 개별표의 동일 (기간·방향·운행일·시각) trip은 dedupe되어 1건, 먼저 나온 place 유지
    matches = [t for t in in_range if t["departure_time"] == "08:00"]
    assert len(matches) == 1
    assert matches[0]["place"] == "2페이지"
    assert matches[0]["period_note"] == "정상근무"  # 통합표(dup)가 아니라 개별표가 유지됨

    # 기간이 다르면(summer_short) 같은 시각이어도 dedupe되지 않고 별도 유지
    short_range = st.query_trips(store, on_date=date(2026, 7, 5))
    assert any(t["departure_time"] == "08:00" and t["period_note"] == "단축근무" for t in short_range)
    assert not any(t["period_note"] == "정상근무" for t in short_range)

    # 기간 밖 날짜: summer_normal/summer_short 모두 빠지고 상시 trip만 남음
    out_of_range = st.query_trips(store, on_date=date(2026, 9, 10))
    assert {t["departure_time"] for t in out_of_range} == {"07:00"}

    # 연말 걸침 구간(12-20~02-28)이 1월 날짜에 유효
    jan = st.query_trips(store, on_date=date(2026, 1, 15))
    assert "23:00" in {t["departure_time"] for t in jan}

    # include_all=True면 기간 필터 없이 전부(단 dedupe는 유지)
    # always, summer_normal, winter_break, summer_short 4건. summer_normal_dup만 dedupe로 제거됨
    # (summer_short는 시각은 같아도 valid_from/valid_to가 달라 별도 유지)
    all_trips = st.query_trips(store, include_all=True)
    assert {t["departure_time"] for t in all_trips} == {"07:00", "08:00", "23:00"}
    assert len(all_trips) == 4


def run():
    check_consensus()
    check_hash_guard()
    check_user_override()
    check_sync()
    check_date_filter_and_dedupe()

    with (
        patch.object(st, "parse_shuttle_image", AsyncMock(return_value=FAKE)),
        patch("httpx.AsyncClient.get", AsyncMock(return_value=_Resp())),
    ):
        doc = asyncio.run(st.ingest_shuttle_image("1페이지", "http://x/1.jpg", 1))
    assert len(doc["trips"]) == 2 and doc["votes"]["unanimous"] and doc["page"] == 1
    assert os.path.exists(st.Config.SHUTTLE_TIMETABLE_PATH)
    assert "1페이지.jpg" in os.listdir(st.Config.SHUTTLE_IMAGE_DIR)

    store = st.load_store()
    assert store["1페이지"]["trips"]
    # FAKE trips는 valid_from/valid_to가 없는 상시 시간표라 include_all 없이도 날짜와 무관하게 나옴
    assert len(st.query_trips(store, day_type="평일")) == 1
    assert st.query_trips(store, origin="제2캠퍼스")[0]["departure_time"] == "16:30"
    assert len(st.query_trips(store, origin="본교")) == 1  # 경유지 포함
    assert st.query_trips(store, destination="본교", day_type="평일")[0]["route"] == "정왕역 ↔ 본교"
    assert st.query_trips(store, route="없음") == []
    assert len(st.query_trips(store, route="제2캠퍼스")) == 1

    # lifespan의 스케줄러/기동 동기화는 실제 iBook을 호출하므로 테스트에서는 차단
    with (
        patch("main.start_scheduler"),
        patch("main.stop_scheduler"),
        patch("main.sync_shuttle_timetables", AsyncMock()),
        TestClient(main.app) as c,
    ):
        r = c.get("/bus/timetable", params={"day_type": "토요일"})
        assert r.status_code == 200, r.text
        assert r.json()["trips"][0]["via"] == ["본교"]
        assert r.json()["sources"][0]["image_url"] == "http://x/1.jpg"
        assert r.json()["sources"][0]["page"] == 1
        assert c.get("/bus/timetable", params={"day_type": "월요일"}).status_code == 422

        # date/all 쿼리: FAKE trips는 기간 표기가 없는 상시 시간표라 어느 날짜를 줘도 그대로 나옴
        r = c.get("/bus/timetable", params={"date": "2026-01-01"})
        assert r.status_code == 200, r.text
        assert len(r.json()["trips"]) == 2
        r = c.get("/bus/timetable", params={"all": "true"})
        assert r.status_code == 200, r.text
        assert len(r.json()["trips"]) == 2
        assert c.get("/bus/timetable", params={"date": "not-a-date"}).status_code == 422
        assert c.post("/bus/timetable/refresh").status_code == 401
        r = c.post("/bus/timetable/refresh", headers={"X-API-Key": "wrong"})
        assert r.status_code == 401

        # 해시 미설정(fail-closed) 시 올바른 키를 보내도 401
        with patch.object(st.Config, "STATIC_INFO_API_KEY_SHA256", ""):
            r = c.post("/bus/timetable/refresh", headers={"X-API-Key": TEST_API_KEY})
            assert r.status_code == 401

        # 라우터가 함수를 직접 import하므로 patch 대상은 app.routers.bus 쪽
        fake_sync = AsyncMock()
        with patch("app.routers.bus.sync_shuttle_timetables", fake_sync):
            r = c.post("/bus/timetable/refresh", headers={"X-API-Key": TEST_API_KEY})
            assert r.status_code == 202, r.text
            r = c.post(
                "/bus/timetable/refresh",
                params={"force": "true"},
                headers={"X-API-Key": TEST_API_KEY},
            )
            assert r.status_code == 202, r.text
        assert [c_.args for c_ in fake_sync.call_args_list] == [(False,), (True,)]

        # PUT/DELETE 인증 실패
        put_body = {
            "title": None,
            "trips": [
                {
                    "direction": "본교 → 정왕역",
                    "day_type": "평일",
                    "day_note": None,
                    "departure_time": "07:00",
                    "departure_time_end": None,
                    "valid_from": None,
                    "valid_to": None,
                    "period_note": None,
                    "boarding_place": None,
                    "note": None,
                }
            ]
        }
        assert c.put("/bus/timetable/새정류장", json=put_body).status_code == 401
        r = c.put(
            "/bus/timetable/새정류장", json=put_body, headers={"X-API-Key": "wrong"}
        )
        assert r.status_code == 401
        assert c.delete("/bus/timetable/새정류장").status_code == 401
        assert (
            c.delete("/bus/timetable/새정류장", headers={"X-API-Key": "wrong"}).status_code
            == 401
        )

        # PUT 저장 → GET에 반영되고 source == "user"
        r = c.put(
            "/bus/timetable/새정류장",
            json=put_body,
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert r.status_code == 200, r.text
        assert r.json()["source"] == "user"

        r = c.get("/bus/timetable")
        src = next(s for s in r.json()["sources"] if s["place"] == "새정류장")
        assert src["source"] == "user"
        assert any(t["place"] == "새정류장" for t in r.json()["trips"])

        # DELETE → 404 및 store에서 사라짐
        r = c.delete("/bus/timetable/새정류장", headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 204
        assert "새정류장" not in st.load_store()
        r = c.delete("/bus/timetable/새정류장", headers={"X-API-Key": TEST_API_KEY})
        assert r.status_code == 404

    print("ok")


if __name__ == "__main__":
    run()
