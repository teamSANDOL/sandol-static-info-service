"""TUK World: 한국공학대학교 총학생회 제휴 할인 식당 API.

조회(GET)는 공개, 등록·수정·삭제·이미지 업로드는 Keycloak 관리자 롤이 필요하다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Config
from app.schemas.responses import AUTH_RESPONSES
from app.schemas.tuk_world import RestaurantCreate, RestaurantOut, RestaurantUpdate
from app.services import tuk_world as service
from app.utils.audit import record_audit
from app.utils.db import get_db
from app.utils.keycloak_auth import require_admin
from app.utils.tuk_world_images import image_path

router = APIRouter(prefix="/tuk-world", tags=["TUK World"])

Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[str, Depends(require_admin)]


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
    return [service.to_out(request, r) for r in service.list_restaurants(db, category)]


@router.get(
    "/restaurants/{restaurant_id}",
    summary="제휴 식당 단건",
    response_model=RestaurantOut,
    responses={Config.HttpStatus.NOT_FOUND: {"description": "식당 없음"}},
)
def get_restaurant(restaurant_id: int, request: Request, db: Db) -> RestaurantOut:
    """식당 하나를 반환합니다."""
    return service.to_out(request, service.get_restaurant_or_404(db, restaurant_id))


@router.post(
    "/restaurants",
    summary="제휴 식당 등록 (관리자)",
    status_code=Config.HttpStatus.CREATED,
    response_model=RestaurantOut,
    responses=AUTH_RESPONSES,
)
def create_restaurant(
    body: RestaurantCreate, request: Request, db: Db, admin: Admin
) -> RestaurantOut:
    """식당을 등록합니다."""
    restaurant = service.create_restaurant(db, body.model_dump(mode="json"), admin)
    record_audit(admin, "tuk_world.restaurant.create", f"id={restaurant.id}")
    return service.to_out(request, restaurant)


@router.patch(
    "/restaurants/{restaurant_id}",
    summary="제휴 식당 수정 (관리자)",
    response_model=RestaurantOut,
    responses={
        **AUTH_RESPONSES,
        Config.HttpStatus.NOT_FOUND: {"description": "식당 없음"},
    },
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
    restaurant = service.update_restaurant(db, restaurant_id, changes, admin)
    record_audit(admin, "tuk_world.restaurant.update", f"id={restaurant_id}")
    return service.to_out(request, restaurant)


@router.delete(
    "/restaurants/{restaurant_id}",
    summary="제휴 식당 삭제 (관리자)",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={
        **AUTH_RESPONSES,
        Config.HttpStatus.NOT_FOUND: {"description": "식당 없음"},
    },
)
def delete_restaurant(restaurant_id: int, db: Db, admin: Admin) -> Response:
    """식당과 그 이미지(파일 포함)를 삭제합니다."""
    service.delete_restaurant(db, restaurant_id)
    record_audit(admin, "tuk_world.restaurant.delete", f"id={restaurant_id}")
    return Response(status_code=Config.HttpStatus.NO_CONTENT)


@router.post(
    "/restaurants/{restaurant_id}/images",
    summary="식당 이미지 업로드 (관리자)",
    status_code=Config.HttpStatus.CREATED,
    response_model=RestaurantOut,
    responses={
        **AUTH_RESPONSES,
        Config.HttpStatus.NOT_FOUND: {"description": "식당 없음"},
        Config.HttpStatus.REQUEST_ENTITY_TOO_LARGE: {"description": "파일 크기 초과"},
        Config.HttpStatus.UNSUPPORTED_MEDIA_TYPE: {
            "description": "허용하지 않는 형식(JPEG/PNG/WEBP만)"
        },
    },
)
def upload_image(
    restaurant_id: int,
    request: Request,
    db: Db,
    admin: Admin,
    file: Annotated[UploadFile, File(description="JPEG/PNG/WEBP 이미지 1장")],
) -> RestaurantOut:
    """이미지 1장을 검증 후 저장합니다. 저장 파일명은 서버가 생성합니다."""
    data = file.file.read(Config.TUK_WORLD_MAX_IMAGE_BYTES + 1)
    restaurant = service.add_image(
        db, restaurant_id, data, file.filename, file.content_type, admin
    )
    record_audit(admin, "tuk_world.image.upload", f"restaurant={restaurant_id}")
    return service.to_out(request, restaurant)


@router.get(
    "/images/{image_id}",
    summary="식당 이미지 파일",
    response_class=FileResponse,
    responses={Config.HttpStatus.NOT_FOUND: {"description": "이미지 없음"}},
)
def get_image(image_id: int, db: Db) -> FileResponse:
    """이미지 파일을 반환합니다."""
    image = service.get_image_or_404(db, image_id)
    return FileResponse(
        image_path(image.filename),
        media_type=image.content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete(
    "/images/{image_id}",
    summary="식당 이미지 삭제 (관리자)",
    status_code=Config.HttpStatus.NO_CONTENT,
    responses={
        **AUTH_RESPONSES,
        Config.HttpStatus.NOT_FOUND: {"description": "이미지 없음"},
    },
)
def delete_image(image_id: int, db: Db, admin: Admin) -> Response:
    """이미지 레코드와 파일을 삭제합니다."""
    service.delete_image(db, image_id, admin)
    record_audit(admin, "tuk_world.image.delete", f"id={image_id}")
    return Response(status_code=Config.HttpStatus.NO_CONTENT)
