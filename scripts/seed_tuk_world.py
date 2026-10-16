"""TUK World 더미 데이터 시드. 테스트 서버 전용.

컨테이너 안에서 실행: python scripts/seed_tuk_world.py
이름이 "[더미]"로 시작하는 식당이 이미 있으면 아무것도 하지 않습니다.
이미지는 업로드 API와 같은 검증·저장 함수를 거칩니다.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.models.tuk_world import Restaurant, RestaurantImage  # noqa: E402
from app.utils.tuk_world_images import save_image, validate_image  # noqa: E402

# (이름, 위도, 경도, 할인 정보). 학교(경기 시흥시 정왕동) 주변 임의 좌표.
DUMMIES = [
    ("[더미] 정왕 국밥집", 37.3412, 126.7335, "학생증 제시 시 전 메뉴 1,000원 할인"),
    (
        "[더미] 산기대 분식",
        37.3398,
        126.7321,
        "포장 10% 할인\n매장 식사 시 음료 1잔 무료",
    ),
    ("[더미] 공대 돈가스", 37.3425, 126.7352, "평일 점심 11~14시 15% 할인"),
    ("[더미] 시흥 짬뽕", 37.3387, 126.7347, "2인 이상 방문 시 군만두 서비스"),
    (
        "[더미] 캠퍼스 카페",
        37.3403,
        126.7309,
        "아메리카노 500원 할인 (텀블러 지참 시 추가 300원)",
    ),
    ("[더미] 정왕 치킨", 37.3431, 126.7318, "배달 제외, 방문 포장 시 2,000원 할인"),
]
IMAGE_COLORS = ["#4a90d9", "#e07b39"]


def dummy_png(label: str, color: str) -> bytes:
    """식별용 단색 PNG를 만듭니다."""
    img = Image.new("RGB", (640, 400), color)
    ImageDraw.Draw(img).text((20, 20), label, fill="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    """더미 식당과 첫 식당의 이미지 2장을 넣습니다."""
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(Restaurant).where(Restaurant.name.like("[더미]%"))):
            print("이미 더미 데이터가 있어 건너뜁니다.")
            return
        rows = []
        for i, (name, lat, lon, discount) in enumerate(DUMMIES):
            rows.append(
                Restaurant(
                    name=name,
                    naver_map_url=f"https://map.naver.com/p/entry/place/{1000 + i}",
                    kakao_map_url=f"https://place.map.kakao.com/{2000 + i}",
                    latitude=lat,
                    longitude=lon,
                    discount_info=discount,
                )
            )
        db.add_all(rows)
        for n, color in enumerate(IMAGE_COLORS, start=1):
            data = dummy_png(f"dummy {n}", color)
            mime, ext = validate_image(data, f"dummy{n}.png", "image/png")
            rows[0].images.append(
                RestaurantImage(filename=save_image(data, ext), content_type=mime)
            )
        db.commit()
        print(f"더미 식당 {len(rows)}건, 이미지 {len(IMAGE_COLORS)}장 추가")


if __name__ == "__main__":
    main()
