"""iBook 뷰어의 페이지 이미지를 내보내는 라우터를 만드는 팩토리입니다.

셔틀버스(bus01)와 주간 식단표(menu02)는 iBook 책 주소만 다르고 응답 형식은 같으므로
라우터를 한 곳에서 만들고 prefix, 주소, 표시 이름만 바꿔 씁니다.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.config import Config, logger
from app.utils import BookDownloader
from app.utils.ibookdownloader import RemoteAccessBlockedError
from app.schemas.responses import all_images_responses, single_image_responses
from app.utils.image_response import (
    ALL_IMAGES_TYPES,
    SINGLE_IMAGE_TYPES,
    build_image_response,
    resolve_response_type,
)


BLOCKED_DETAIL = "학교 서버가 요청을 거부해 재시작 전까지 이미지를 가져오지 않습니다."
BLOCKED_RESPONSES: dict[int | str, dict[str, str]] = {
    Config.HttpStatus.SERVICE_UNAVAILABLE: {"description": BLOCKED_DETAIL}
}


def _blocked_503() -> HTTPException:
    """차단 래치가 걸렸을 때 돌려줄 503 예외를 만듭니다."""
    return HTTPException(
        status_code=Config.HttpStatus.SERVICE_UNAVAILABLE, detail=BLOCKED_DETAIL
    )


def create_ibook_image_router(prefix: str, book_url: str, label: str) -> APIRouter:
    """iBook 책 하나의 페이지 이미지를 내보내는 라우터를 만듭니다.

    Args:
        prefix (str): 라우터 prefix. 예: "/bus", "/meal"
        book_url (str): iBook 뷰어 주소. 예: Config.SHUTTLE_URL
        label (str): 로그와 에러 메시지에 쓰는 표시 이름. 예: "버스", "주간 식단표"

    Returns:
        APIRouter: GET {prefix}/images, GET {prefix}/image/{index} 를 가진 라우터
    """
    router = APIRouter(prefix=prefix)
    file_stem = prefix.strip("/")

    @router.get(
        "/images",
        responses={**all_images_responses(label), **BLOCKED_RESPONSES},
        response_class=Response,
    )
    async def get_all_images(request: Request):
        """모든 이미지를 Accept 헤더에 따라 다양한 형식으로 반환합니다."""
        response_type = resolve_response_type(request, ALL_IMAGES_TYPES)
        logger.info("모든 %s 이미지 요청 수신", label)

        try:
            image_urls = await BookDownloader(book_url).fetch_image_list()
            if not image_urls:
                raise HTTPException(
                    status_code=Config.HttpStatus.NOT_FOUND,
                    detail=f"{label} 이미지가 없습니다.",
                )
            return await build_image_response(image_urls, response_type, file_stem)
        except RemoteAccessBlockedError as e:
            raise _blocked_503() from e

    @router.get(
        "/image/{index}",
        responses={**single_image_responses(label), **BLOCKED_RESPONSES},
        response_class=Response,
    )
    async def get_image_by_index(index: int, request: Request):
        """특정 인덱스(1부터 시작)의 이미지를 Accept 헤더에 따라 반환합니다."""
        response_type = resolve_response_type(request, SINGLE_IMAGE_TYPES)
        logger.info("%s 이미지 요청 (index=%d)", label, index)

        try:
            image_urls = await BookDownloader(book_url).fetch_image_list()
            if index < 1 or index > len(image_urls):
                raise HTTPException(
                    status_code=Config.HttpStatus.NOT_FOUND,
                    detail="해당 index의 이미지가 없습니다.",
                )
            return await build_image_response(
                image_urls[index - 1], response_type, file_stem
            )
        except RemoteAccessBlockedError as e:
            raise _blocked_503() from e

    return router
