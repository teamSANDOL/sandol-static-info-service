"""TUK World 식당·이미지 업무 로직. REST 라우터가 사용한다."""

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import Config
from app.models.tuk_world import Restaurant, RestaurantImage
from app.schemas.tuk_world import RestaurantImageOut, RestaurantOut
from app.utils.tuk_world_images import (
    ImageValidationError,
    remove_image,
    save_image,
    validate_image,
)

_UNSUPPORTED = Config.HttpStatus.UNSUPPORTED_MEDIA_TYPE
# ImageValidationError 메시지 -> (HTTP 상태 코드, 사용자에게 보이는 설명)
IMAGE_ERRORS: dict[str, tuple[int, str]] = {
    "empty_file": (_UNSUPPORTED, "빈 파일입니다."),
    "file_too_large": (
        Config.HttpStatus.REQUEST_ENTITY_TOO_LARGE,
        "파일 크기가 제한을 초과했습니다.",
    ),
    "unsupported_extension": (_UNSUPPORTED, "허용하지 않는 확장자입니다."),
    "invalid_image": (_UNSUPPORTED, "이미지로 읽을 수 없는 파일입니다."),
    "unsupported_image_format": (_UNSUPPORTED, "JPEG/PNG/WEBP만 업로드할 수 있습니다."),
    "content_type_mismatch": (
        _UNSUPPORTED,
        "Content-Type이 실제 이미지 형식과 다릅니다.",
    ),
}
# PATCH로 null을 보내 지울 수 없는 필드
NOT_NULL_FIELDS = ("name", "latitude", "longitude")


def to_out(request: Request, restaurant: Restaurant) -> RestaurantOut:
    """식당 ORM 객체를 응답 스키마로 바꿉니다. 이미지 URL은 공개 주소 기준으로 만듭니다.

    Args:
        request: 현재 요청(root_path를 읽는 데 사용).
        restaurant: 변환할 식당.

    Returns:
        이미지 목록을 포함한 응답 객체.
    """
    root = Config.PUBLIC_BASE_URL + request.scope.get("root_path", "")
    return RestaurantOut(
        id=restaurant.id,
        name=restaurant.name,
        category=restaurant.category,
        address=restaurant.address,
        naver_map_url=restaurant.naver_map_url,
        kakao_map_url=restaurant.kakao_map_url,
        latitude=restaurant.latitude,
        longitude=restaurant.longitude,
        discount_info=restaurant.discount_info,
        created_at=restaurant.created_at,
        updated_at=restaurant.updated_at,
        images=[
            RestaurantImageOut(
                id=img.id,
                url=f"{root}/tuk-world/images/{img.id}",
                content_type=img.content_type,
                created_at=img.created_at,
            )
            for img in restaurant.images
        ],
    )


def get_restaurant_or_404(db: Session, restaurant_id: int) -> Restaurant:
    """식당을 id로 조회합니다.

    Raises:
        HTTPException: 식당이 없으면 404.
    """
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise HTTPException(Config.HttpStatus.NOT_FOUND, "식당을 찾을 수 없습니다.")
    return restaurant


def get_image_or_404(db: Session, image_id: int) -> RestaurantImage:
    """이미지 레코드를 id로 조회합니다.

    Raises:
        HTTPException: 이미지가 없으면 404.
    """
    image = db.get(RestaurantImage, image_id)
    if image is None:
        raise HTTPException(Config.HttpStatus.NOT_FOUND, "이미지를 찾을 수 없습니다.")
    return image


def list_restaurants(db: Session, category: str | None) -> list[Restaurant]:
    """식당을 id 순으로 조회합니다.

    Args:
        db: DB 세션.
        category: 주면 분류가 정확히 일치하는 식당만.

    Returns:
        식당 목록.
    """
    query = select(Restaurant).order_by(Restaurant.id)
    if category is not None:
        query = query.where(Restaurant.category == category)
    return list(db.scalars(query).all())


def create_restaurant(db: Session, values: dict[str, Any], admin: str) -> Restaurant:
    """식당을 저장합니다.

    Args:
        db: DB 세션.
        values: 식당 필드 값(JSON 직렬화 가능한 dict).
        admin: 수정자 Keycloak sub.

    Returns:
        커밋된 식당.
    """
    restaurant = Restaurant(**values, updated_by=admin)
    db.add(restaurant)
    db.commit()
    return restaurant


def update_restaurant(
    db: Session, restaurant_id: int, changes: dict[str, Any], admin: str
) -> Restaurant:
    """보낸 필드만 식당에 반영합니다.

    Args:
        db: DB 세션.
        restaurant_id: 수정할 식당 id.
        changes: 바꿀 필드와 값.
        admin: 수정자 Keycloak sub.

    Returns:
        커밋된 식당.

    Raises:
        HTTPException: 비울 수 없는 필드에 null이 오면 422, 식당이 없으면 404.
    """
    nulls = [k for k in NOT_NULL_FIELDS if k in changes and changes[k] is None]
    if nulls:
        raise HTTPException(
            Config.HttpStatus.UNPROCESSABLE_ENTITY,
            f"비울 수 없는 필드입니다: {', '.join(nulls)}",
        )
    restaurant = get_restaurant_or_404(db, restaurant_id)
    for key, value in changes.items():
        setattr(restaurant, key, value)
    restaurant.updated_by = admin
    db.commit()
    return restaurant


def delete_restaurant(db: Session, restaurant_id: int) -> None:
    """식당과 그 이미지 레코드를 지우고, 커밋 뒤 이미지 파일도 지웁니다.

    Raises:
        HTTPException: 식당이 없으면 404.
    """
    restaurant = get_restaurant_or_404(db, restaurant_id)
    filenames = [img.filename for img in restaurant.images]
    db.delete(restaurant)
    db.commit()
    for name in filenames:
        remove_image(name)


def add_image(
    db: Session,
    restaurant_id: int,
    data: bytes,
    original_name: str | None,
    declared_type: str | None,
    admin: str,
) -> Restaurant:
    """이미지를 검증·저장하고 식당에 연결합니다. 커밋 실패 시 저장한 파일을 지웁니다.

    Args:
        db: DB 세션.
        restaurant_id: 이미지를 붙일 식당 id.
        data: 업로드된 바이트.
        original_name: 업로드 파일명(확장자 검사용).
        declared_type: 업로드 Content-Type.
        admin: 수정자 Keycloak sub.

    Returns:
        갱신된 식당.

    Raises:
        HTTPException: 식당이 없으면 404, 이미지가 검증을 통과하지 못하면 413/415.
    """
    restaurant = get_restaurant_or_404(db, restaurant_id)
    try:
        mime, ext = validate_image(data, original_name, declared_type)
    except ImageValidationError as exc:
        code, detail = IMAGE_ERRORS.get(
            str(exc), (_UNSUPPORTED, "업로드할 수 없는 파일입니다.")
        )
        raise HTTPException(code, detail) from exc
    filename = save_image(data, ext)
    restaurant.images.append(
        RestaurantImage(filename=filename, content_type=mime, updated_by=admin)
    )
    restaurant.updated_at = datetime.now(UTC)
    restaurant.updated_by = admin
    try:
        db.commit()
    except Exception:
        remove_image(filename)
        raise
    db.refresh(restaurant)
    return restaurant


def delete_image(db: Session, image_id: int, admin: str) -> None:
    """이미지 레코드를 지우고 소속 식당의 수정 정보를 갱신한 뒤 파일을 지웁니다.

    Raises:
        HTTPException: 이미지가 없으면 404.
    """
    image = get_image_or_404(db, image_id)
    filename = image.filename
    image.restaurant.updated_at = datetime.now(UTC)
    image.restaurant.updated_by = admin
    db.delete(image)
    db.commit()
    remove_image(filename)
