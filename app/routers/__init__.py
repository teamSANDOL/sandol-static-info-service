"""API 서버를 구성하는 모듈들"""

from app.routers.bus import router as bus_router
from app.routers.meal import router as meal_router
from app.routers.organization import router as organization_router
from app.routers.tuk_world import router as tuk_world_router

__all__ = [
    "bus_router",
    "meal_router",
    "organization_router",
    "tuk_world_router",
]
