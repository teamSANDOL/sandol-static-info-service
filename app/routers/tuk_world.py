"""TUK World: 한국공학대학교 총학생회 제휴 할인 식당 API.

조회(GET)는 공개, 등록·수정·삭제·이미지 업로드는 Keycloak 관리자 롤이 필요하다.
"""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Config, logger
from app.models.tuk_world import Restaurant, RestaurantImage, get_db
from app.schemas.tuk_world import (
    RestaurantCreate,
    RestaurantImageOut,
    RestaurantOut,
    RestaurantUpdate,
)
from app.utils.keycloak_auth import require_admin
from app.utils.tuk_world_images import (
    ImageValidationError,
    image_path,
    remove_image,
    save_image,
    validate_image,
)

router = APIRouter(prefix="/tuk-world", tags=["TUK World"])

Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[str, Depends(require_admin)]
AUTH_RESPONSES: dict[int | str, dict[str, str]] = {
    401: {"description": "Bearer 토큰 없음/무효"},
    403: {"description": "관리자 롤 없음"},
    503: {"description": "인증 미설정(fail-closed) 또는 JWKS 조회 실패"},
}
NOT_NULL_FIELDS = ("name", "latitude", "longitude")


def _to_out(request: Request, restaurant: Restaurant) -> RestaurantOut:
    root = request.scope.get("root_path", "")
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


def _get_or_404(db: Session, restaurant_id: int) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise HTTPException(404, "restaurant_not_found")
    return restaurant


@router.get(
    "/restaurants", summary="제휴 식당 목록", response_model=list[RestaurantOut]
)
def list_restaurants(
    request: Request,
    db: Db,
    category: Annotated[
        str | None, Query(description="분류 정확히 일치 (예: 식당·술집·여가)")
    ] = None,
) -> list[RestaurantOut]:
    """등록된 제휴 식당을 id 순으로 반환합니다. category를 주면 그 분류만."""
    query = select(Restaurant).order_by(Restaurant.id)
    if category is not None:
        query = query.where(Restaurant.category == category)
    rows = db.scalars(query).all()
    return [_to_out(request, r) for r in rows]


@router.get(
    "/restaurants/{restaurant_id}",
    summary="제휴 식당 단건",
    response_model=RestaurantOut,
    responses={404: {"description": "식당 없음"}},
)
def get_restaurant(restaurant_id: int, request: Request, db: Db) -> RestaurantOut:
    """식당 하나를 반환합니다."""
    return _to_out(request, _get_or_404(db, restaurant_id))


@router.post(
    "/restaurants",
    summary="제휴 식당 등록 (관리자)",
    status_code=201,
    response_model=RestaurantOut,
    responses=AUTH_RESPONSES,
)
def create_restaurant(
    body: RestaurantCreate, request: Request, db: Db, admin: Admin
) -> RestaurantOut:
    """식당을 등록합니다."""
    restaurant = Restaurant(**body.model_dump(mode="json"))
    db.add(restaurant)
    db.commit()
    logger.info("TUK World 식당 등록 id=%s by=%s", restaurant.id, admin)
    return _to_out(request, restaurant)


@router.patch(
    "/restaurants/{restaurant_id}",
    summary="제휴 식당 수정 (관리자)",
    response_model=RestaurantOut,
    responses={**AUTH_RESPONSES, 404: {"description": "식당 없음"}},
)
def update_restaurant(
    restaurant_id: int,
    body: RestaurantUpdate,
    request: Request,
    db: Db,
    admin: Admin,
) -> RestaurantOut:
    """보낸 필드만 수정합니다. name·latitude·longitude는 null로 지울 수 없습니다."""
    changes = body.model_dump(mode="json", exclude_unset=True)
    nulls = [k for k in NOT_NULL_FIELDS if k in changes and changes[k] is None]
    if nulls:
        raise HTTPException(422, f"not_nullable: {', '.join(nulls)}")
    restaurant = _get_or_404(db, restaurant_id)
    for key, value in changes.items():
        setattr(restaurant, key, value)
    db.commit()
    logger.info("TUK World 식당 수정 id=%s by=%s", restaurant_id, admin)
    return _to_out(request, restaurant)


@router.delete(
    "/restaurants/{restaurant_id}",
    summary="제휴 식당 삭제 (관리자)",
    status_code=204,
    responses={**AUTH_RESPONSES, 404: {"description": "식당 없음"}},
)
def delete_restaurant(restaurant_id: int, db: Db, admin: Admin) -> Response:
    """식당과 그 이미지(파일 포함)를 삭제합니다."""
    restaurant = _get_or_404(db, restaurant_id)
    filenames = [img.filename for img in restaurant.images]
    db.delete(restaurant)
    db.commit()
    for name in filenames:
        remove_image(name)
    logger.info("TUK World 식당 삭제 id=%s by=%s", restaurant_id, admin)
    return Response(status_code=204)


@router.post(
    "/restaurants/{restaurant_id}/images",
    summary="식당 이미지 업로드 (관리자)",
    status_code=201,
    response_model=RestaurantOut,
    responses={
        **AUTH_RESPONSES,
        404: {"description": "식당 없음"},
        413: {"description": "파일 크기 초과"},
        415: {"description": "허용하지 않는 형식(JPEG/PNG/WEBP만)"},
    },
)
async def upload_image(
    restaurant_id: int,
    request: Request,
    db: Db,
    admin: Admin,
    file: Annotated[UploadFile, File(description="JPEG/PNG/WEBP 이미지 1장")],
) -> RestaurantOut:
    """이미지 1장을 검증 후 저장합니다. 저장 파일명은 서버가 생성합니다."""
    restaurant = _get_or_404(db, restaurant_id)
    data = await file.read(Config.TUK_WORLD_MAX_IMAGE_BYTES + 1)
    try:
        mime, ext = validate_image(data, file.filename, file.content_type)
    except ImageValidationError as exc:
        code = 413 if str(exc) == "file_too_large" else 415
        raise HTTPException(code, str(exc)) from exc
    filename = save_image(data, ext)
    restaurant.images.append(RestaurantImage(filename=filename, content_type=mime))
    try:
        db.commit()
    except Exception:
        remove_image(filename)
        raise
    db.refresh(restaurant)
    logger.info("TUK World 이미지 업로드 restaurant=%s by=%s", restaurant_id, admin)
    return _to_out(request, restaurant)


@router.get(
    "/images/{image_id}",
    summary="식당 이미지 파일",
    response_class=FileResponse,
    responses={404: {"description": "이미지 없음"}},
)
def get_image(image_id: int, db: Db) -> FileResponse:
    """이미지 파일을 반환합니다."""
    image = db.get(RestaurantImage, image_id)
    if image is None:
        raise HTTPException(404, "image_not_found")
    return FileResponse(
        image_path(image.filename),
        media_type=image.content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete(
    "/images/{image_id}",
    summary="식당 이미지 삭제 (관리자)",
    status_code=204,
    responses={**AUTH_RESPONSES, 404: {"description": "이미지 없음"}},
)
def delete_image(image_id: int, db: Db, admin: Admin) -> Response:
    """이미지 레코드와 파일을 삭제합니다."""
    image = db.get(RestaurantImage, image_id)
    if image is None:
        raise HTTPException(404, "image_not_found")
    filename = image.filename
    db.delete(image)
    db.commit()
    remove_image(filename)
    logger.info("TUK World 이미지 삭제 id=%s by=%s", image_id, admin)
    return Response(status_code=204)
