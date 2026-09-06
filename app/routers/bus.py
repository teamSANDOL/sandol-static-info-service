"""셔틀버스 시간표 이미지 라우터입니다. GET /bus/images, GET /bus/image/{index}"""

from app.config import Config
from app.routers.ibook_images import create_ibook_image_router

router = create_ibook_image_router("/bus", Config.SHUTTLE_URL, "버스")
