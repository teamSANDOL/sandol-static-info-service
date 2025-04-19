from typing import Literal, Union
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response

from app.config import Config, logger
from app.utils import BookDownloader
from app.utils.image_response import build_image_response

router = APIRouter(prefix="/bus")


@router.get(
    "/images",
    responses={
        200: {
            "description": "모든 버스 이미지 반환 (응답 타입에 따라 json/base64/zip 등 다양하게 반환됩니다)",
            "content": {
                Config.Accept.JSON: {
                    "example": {
                        "image_urls": [
                            "https://example.com/img1.jpg",
                            "https://example.com/img2.jpg",
                        ]
                    }
                },
                Config.Accept.BASE64: {
                    "example": {
                        "image_base64_list": [
                            "/9j/4AAQSkZJRgAB...",
                            "/9j/4AAQSkZJRgAC...",
                        ]
                    }
                },
                Config.Accept.ZIP: {"schema": {"type": "string", "format": "binary"}},
                Config.Accept.OCTET_STREAM: {
                    "schema": {"type": "string", "format": "binary"}
                },
                "text/plain": {"example": "https://example.com/img1.jpg"},
                Config.ImageType.JPEG: {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        },
        404: {"description": "버스 이미지가 없습니다."},
        406: {"description": "지원되지 않는 Accept 헤더입니다."},
    },
    response_class=Response,
)
async def get_all_bus_images(request: Request):
    """모든 버스 이미지들을 Accept 헤더에 따라 다양한 형식으로 반환합니다."""
    accept_header = request.headers.get("accept", "").lower()
    logger.info("모든 버스 이미지 요청 수신")

    # MIME 타입에 따라 response_type 결정
    if Config.Accept.JSON in accept_header:
        response_type = "json"
    elif Config.Accept.BASE64 in accept_header:
        response_type = "base64"
    elif Config.Accept.ZIP in accept_header:
        response_type = "zip"
    elif Config.Accept.OCTET_STREAM in accept_header:
        response_type = "octet-stream"
    else:
        raise HTTPException(status_code=406, detail="지원되지 않는 Accept 헤더입니다.")

    downloader = BookDownloader(Config.SHUTTLE_URL)
    image_urls = await downloader.fetch_image_list()

    if not image_urls:
        raise HTTPException(status_code=404, detail="버스 이미지가 없습니다.")

    return await build_image_response(image_urls, response_type)


@router.get(
    "/image/{index}",
    responses={
        200: {
            "description": "특정 인덱스의 버스 이미지 반환 (Accept 헤더에 따라 포맷이 달라짐)",
            "content": {
                Config.Accept.JSON: {
                    "example": {"image_url": "https://example.com/img1.jpg"}
                },
                Config.Accept.BASE64: {
                    "example": {"image_base64": "/9j/4AAQSkZJRgAB..."}
                },
                Config.Accept.OCTET_STREAM: {
                    "schema": {"type": "string", "format": "binary"}
                },
                Config.ImageType.JPEG: {
                    "schema": {"type": "string", "format": "binary"}
                },
                "text/plain": {"example": "https://example.com/img1.jpg"},
            },
        },
        404: {"description": "해당 index의 이미지가 없습니다."},
        406: {"description": "지원되지 않는 Accept 헤더입니다."},
    },
    response_class=Response,
)
async def get_bus_image_by_index(index: int, request: Request):
    """특정 인덱스의 버스 이미지를 Accept 헤더에 따라 다양한 형식으로 반환합니다."""
    accept_header = request.headers.get("accept", "").lower()
    logger.info(f"버스 이미지 요청 (index={index})")

    if Config.Accept.JSON in accept_header:
        response_type = "json"
    elif Config.Accept.BASE64 in accept_header:
        response_type = "base64"
    elif Config.Accept.OCTET_STREAM in accept_header:
        response_type = "octet-stream"
    elif Config.Accept.ZIP in accept_header:
        response_type = "zip"
    elif "text/plain" in accept_header:
        response_type = "text"
    elif Config.ImageType.JPEG in accept_header:
        response_type = "jpeg"
    else:
        raise HTTPException(status_code=406, detail="지원되지 않는 Accept 헤더입니다.")

    downloader = BookDownloader(Config.SHUTTLE_URL)
    image_urls = await downloader.fetch_image_list()

    if index < 1 or index > len(image_urls):
        raise HTTPException(status_code=404, detail="해당 index의 이미지가 없습니다.")

    return await build_image_response(image_urls[index - 1], response_type)
