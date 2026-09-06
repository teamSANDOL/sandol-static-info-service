"""주간 식단표 이미지 라우터입니다. GET /meal/images, GET /meal/image/{index}

학교 iBook의 주간 식단표(menu02) 페이지 이미지를 그대로 내보냅니다.
meal-service가 오늘 요일만 파싱해 저장하므로 주간 전체 표는 이 이미지가 유일합니다.
"""

from app.config import Config
from app.routers.ibook_images import create_ibook_image_router

router = create_ibook_image_router("/meal", Config.WEEKLY_MENU_URL, "주간 식단표")
