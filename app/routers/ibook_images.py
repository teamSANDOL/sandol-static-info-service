"""iBook 뷰어의 페이지 이미지를 내보내는 라우터를 만드는 팩토리입니다.

셔틀버스(bus01)와 주간 식단표(menu02)는 iBook 책 주소만 다르고 응답 형식은 같으므로
라우터를 한 곳에서 만들고 prefix, 주소, 표시 이름만 바꿔 씁니다.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.config import Config, logger
from app.utils import BookDownloader
from app.utils.image_response import build_image_response

# Accept 헤더 -> response_type. 순서가 우선순위입니다.
_ALL_IMAGES_TYPES: list[tuple[str, str]] = [
    (Config.Accept.JSON, "json"),
    (Config.Accept.BASE64, "base64"),
    (Config.Accept.ZIP, "zip"),
    (Config.Accept.OCTET_STREAM, "octet-stream"),
]
_SINGLE_IMAGE_TYPES: list[tuple[str, str]] = [
    (Config.Accept.JSON, "json"),
    (Config.Accept.BASE64, "base64"),
    (Config.Accept.OCTET_STREAM, "octet-stream"),
    (Config.Accept.ZIP, "zip"),
    ("text/plain", "text"),
    (Config.ImageType.JPEG, "jpeg"),
]


def _resolve_response_type(request: Request, table: list[tuple[str, str]]) -> str:
    accept_header = request.headers.get("accept", "").lower()
    for mime, response_type in table:
        if mime in accept_header:
            return response_type
    raise HTTPException(
        status_code=Config.HttpStatus.NOT_ACCEPTABLE,
        detail="지원되지 않는 Accept 헤더입니다.",
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
        responses={
            Config.HttpStatus.OK: {
                "description": f"모든 {label} 이미지 반환 (Accept 헤더에 따라 json/base64/zip 등)",
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
                    Config.Accept.ZIP: {
                        "schema": {"type": "string", "format": "binary"}
                    },
                    Config.Accept.OCTET_STREAM: {
                        "schema": {"type": "string", "format": "binary"}
                    },
                },
            },
            Config.HttpStatus.NOT_FOUND: {"description": f"{label} 이미지가 없습니다."},
            Config.HttpStatus.NOT_ACCEPTABLE: {
                "description": "지원되지 않는 Accept 헤더입니다."
            },
        },
        response_class=Response,
    )
    async def get_all_images(request: Request):
        """모든 이미지를 Accept 헤더에 따라 다양한 형식으로 반환합니다."""
        response_type = _resolve_response_type(request, _ALL_IMAGES_TYPES)
        logger.info("모든 %s 이미지 요청 수신", label)

        image_urls = await BookDownloader(book_url).fetch_image_list()
        if not image_urls:
            raise HTTPException(
                status_code=Config.HttpStatus.NOT_FOUND,
                detail=f"{label} 이미지가 없습니다.",
            )
        return await build_image_response(image_urls, response_type, file_stem)

    @router.get(
        "/image/{index}",
        responses={
            Config.HttpStatus.OK: {
                "description": f"특정 인덱스의 {label} 이미지 반환 (Accept 헤더에 따라 포맷이 달라짐)",
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
            Config.HttpStatus.NOT_FOUND: {
                "description": "해당 index의 이미지가 없습니다."
            },
            Config.HttpStatus.NOT_ACCEPTABLE: {
                "description": "지원되지 않는 Accept 헤더입니다."
            },
        },
        response_class=Response,
    )
    async def get_image_by_index(index: int, request: Request):
        """특정 인덱스(1부터 시작)의 이미지를 Accept 헤더에 따라 반환합니다."""
        response_type = _resolve_response_type(request, _SINGLE_IMAGE_TYPES)
        logger.info("%s 이미지 요청 (index=%d)", label, index)

        image_urls = await BookDownloader(book_url).fetch_image_list()
        if index < 1 or index > len(image_urls):
            raise HTTPException(
                status_code=Config.HttpStatus.NOT_FOUND,
                detail="해당 index의 이미지가 없습니다.",
            )
        return await build_image_response(
            image_urls[index - 1], response_type, file_stem
        )

    return router
