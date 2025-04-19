"""Sandol의 메인 애플리케이션 파일입니다."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from app.routers import bus_router
from app.config.config import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI의 lifespan 이벤트 핸들러"""
    logger.info("🚀 서비스 시작:")

    yield  # FastAPI가 실행 중인 동안 유지됨

    # 애플리케이션 종료 시 로그 출력
    logger.info("🛑 서비스 종료:")


# lifespan 적용
app = FastAPI(lifespan=lifespan, root_path="/static_info")
app.include_router(bus_router)


@app.get("/")
async def root():
    """루트 엔드포인트입니다."""
    logger.info("Root endpoint accessed")
    return {"test": "Hello Sandol"}


if __name__ == "__main__":
    HOST = "0.0.0.0"
    PORT = 5600

    logger.info("Starting Sandol server on %s:%s", HOST, PORT)
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
