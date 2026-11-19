"""여러 라우터가 공유하는 OpenAPI responses 선언."""

from typing import Any

from app.config import Config

# FastAPI `responses=` 인자의 타입과 같다.
OpenApiResponses = dict[int | str, dict[str, Any]]

# require_admin 의존성을 쓰는 쓰기 엔드포인트가 낼 수 있는 인증 오류.
AUTH_RESPONSES: OpenApiResponses = {
    Config.HttpStatus.UNAUTHORIZED: {"description": "Bearer 토큰 없음/무효"},
    Config.HttpStatus.FORBIDDEN: {"description": "관리자 롤 없음"},
    Config.HttpStatus.SERVICE_UNAVAILABLE: {
        "description": "인증 미설정(fail-closed) 또는 JWKS 조회 실패"
    },
}


def all_images_responses(label: str) -> OpenApiResponses:
    """`GET {prefix}/images`의 responses 선언을 만듭니다.

    Args:
        label: 설명에 쓰는 표시 이름(예: "버스").

    Returns:
        OpenAPI responses dict.
    """
    return {
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
                Config.Accept.ZIP: {"schema": {"type": "string", "format": "binary"}},
                Config.Accept.OCTET_STREAM: {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        },
        Config.HttpStatus.NOT_FOUND: {"description": f"{label} 이미지가 없습니다."},
        Config.HttpStatus.NOT_ACCEPTABLE: {
            "description": "지원되지 않는 Accept 헤더입니다."
        },
    }


def single_image_responses(label: str) -> OpenApiResponses:
    """`GET {prefix}/image/{index}`의 responses 선언을 만듭니다.

    Args:
        label: 설명에 쓰는 표시 이름(예: "버스").

    Returns:
        OpenAPI responses dict.
    """
    return {
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
        Config.HttpStatus.NOT_FOUND: {"description": "해당 index의 이미지가 없습니다."},
        Config.HttpStatus.NOT_ACCEPTABLE: {
            "description": "지원되지 않는 Accept 헤더입니다."
        },
    }
