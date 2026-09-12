"""Sandol의 메인 애플리케이션 파일입니다."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from app.routers import bus_router, meal_router, organization_router
from app.config.config import logger
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.utils.shuttle_timetable import sync_shuttle_timetables


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI의 lifespan 이벤트 핸들러"""
    logger.info("🚀 서비스 시작:")
    start_scheduler()
    # 배포 직후 수동 refresh 없이도 채워지도록 기동 시 1회 동기화 (참조를 잡아둬야 GC되지 않음)
    startup_sync = asyncio.create_task(sync_shuttle_timetables())

    yield  # FastAPI가 실행 중인 동안 유지됨

    startup_sync.cancel()
    stop_scheduler()

    # 애플리케이션 종료 시 로그 출력
    logger.info("🛑 서비스 종료:")


# lifespan 적용
app = FastAPI(lifespan=lifespan, root_path="/static-info")
app.include_router(bus_router)
app.include_router(meal_router)
app.include_router(organization_router)


@app.get("/")
async def root():
    """루트 엔드포인트입니다."""
    logger.info("Root endpoint accessed")
    return {"test": "Hello Sandol"}


@app.get("/health")
async def health_check():
    """헬스 체크 엔드포인트입니다."""
    return {"status": "ok"}


if __name__ == "__main__":
    HOST = "0.0.0.0"
    PORT = 5600

    logger.info("Starting Sandol server on %s:%s", HOST, PORT)
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
