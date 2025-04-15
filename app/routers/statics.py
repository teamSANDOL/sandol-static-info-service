from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse


from app.config import Config, logger
from app.utils import BookDownloader
from app.utils.image_response import build_image_response_from_url


bus_api = APIRouter(prefix="/bus")


@bus_api.get("/images")
async def get_all_bus_images(request: Request):
    """모든 버스 이미지들을 Content-Type에 따라 반환합니다."""
    content_type = request.headers.get("content-type", "").lower()
    logger.info("모든 버스 이미지 요청 수신")

    if "application/json" in content_type:
        response_type = "json"
    elif "application/base64" in content_type:
        response_type = "base64"
    elif "application/octet-stream" in content_type:
        response_type = "binary"
    else:
        raise HTTPException(status_code=406, detail="지원되지 않는 Content-Type입니다.")

    downloader = BookDownloader(Config.SHUTTLE_URL)
    image_urls = await downloader.fetch_image_list()
    image_urls = image_urls[::-1]  # 최신 이미지가 마지막에

    if not image_urls:
        raise HTTPException(status_code=404, detail="버스 이미지가 없습니다.")

    return await build_multi_image_response(image_urls, response_type)


@bus_api.get("/image/{index}")
async def get_bus_image_by_index(index: int, request: Request):
    """특정 인덱스의 버스 이미지 반환"""
    content_type = request.headers.get("content-type", "").lower()
    logger.info(f"버스 이미지 요청 (index={index})")

    if "application/json" in content_type:
        response_type = "json"
    elif "application/base64" in content_type:
        response_type = "base64"
    elif "application/octet-stream" in content_type:
        response_type = "binary"
    else:
        raise HTTPException(status_code=406, detail="지원되지 않는 Content-Type입니다.")

    downloader = BookDownloader(Config.SHUTTLE_URL)
    image_urls = await downloader.fetch_image_list()
    image_urls = image_urls[::-1]

    if index < 1 or index > len(image_urls):
        raise HTTPException(status_code=404, detail="해당 index의 이미지가 없습니다.")

    target_url = image_urls[index - 1]

    if response_type == "json":
        return JSONResponse(content={"image_url": target_url})

    return await build_image_response_from_url(target_url, response_type)
