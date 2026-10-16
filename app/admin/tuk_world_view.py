"""TUK World 관리 화면: 식당 CRUD와 이미지 업로드·삭제.

이미지 검증·저장·삭제는 REST API와 같은 app.utils.tuk_world_images를 쓴다.
Restaurant에는 updated_by 컬럼이 없어 작성자는 감사 로그(admin_audit)로만 남긴다.
"""

from typing import Any

from markupsafe import Markup, escape
from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter
from starlette.datastructures import UploadFile
from starlette.requests import Request
from wtforms import FileField, FloatField, Form
from wtforms.validators import NumberRange, Optional, Regexp

from app.admin.auth import log_admin_action
from app.config import Config
from app.database import SessionLocal
from app.models.tuk_world import Restaurant, RestaurantImage
from app.utils.tuk_world_images import (
    ImageValidationError,
    remove_image,
    save_image,
    validate_image,
)

_URL = [Optional(), Regexp(r"^https?://", message="http(s):// 주소여야 합니다.")]


def _thumb(image_id: int) -> Markup:
    src = f"{Config.ROOT_PATH}/tuk-world/images/{image_id}"
    return Markup('<img src="{}" style="max-height:80px;margin:2px">').format(src)


def _thumbs(model: Any, attr: Any) -> Markup:
    return Markup("").join(_thumb(i.id) for i in model.images)


def _preview(model: Any, attr: Any) -> Markup:
    return _thumb(model.id)


class RestaurantAdmin(ModelView, model=Restaurant):
    """제휴 식당. 삭제하면 이미지 레코드와 파일도 함께 지운다(REST와 동일)."""

    name = "제휴 식당"
    name_plural = "제휴 식당"
    icon = "fa-solid fa-utensils"
    column_list = [
        Restaurant.id,
        Restaurant.category,
        Restaurant.name,
        Restaurant.address,
        Restaurant.images,
    ]
    column_searchable_list = [Restaurant.name, Restaurant.category, Restaurant.address]
    column_filters = [AllUniqueStringValuesFilter(Restaurant.category, title="분류")]
    column_default_sort = [(Restaurant.id, True)]
    column_details_list = [
        Restaurant.id,
        Restaurant.category,
        Restaurant.name,
        Restaurant.address,
        Restaurant.naver_map_url,
        Restaurant.kakao_map_url,
        Restaurant.latitude,
        Restaurant.longitude,
        Restaurant.discount_info,
        Restaurant.images,
        Restaurant.created_at,
        Restaurant.updated_at,
    ]
    column_formatters = {Restaurant.images: _thumbs}
    column_formatters_detail = column_formatters
    form_columns = [
        Restaurant.category,
        Restaurant.name,
        Restaurant.address,
        Restaurant.naver_map_url,
        Restaurant.kakao_map_url,
        Restaurant.latitude,
        Restaurant.longitude,
        Restaurant.discount_info,
    ]
    # sqladmin 0.24는 Float 컬럼 변환기가 없어 직접 지정한다.
    form_overrides = {"latitude": FloatField, "longitude": FloatField}
    form_args = {
        "naver_map_url": {"validators": _URL},
        "kakao_map_url": {"validators": _URL},
        "latitude": {"validators": [NumberRange(-90, 90, "위도는 -90~90입니다.")]},
        "longitude": {"validators": [NumberRange(-180, 180, "경도는 -180~180입니다.")]},
    }

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """감사 로그."""
        action = "create" if is_created else "update"
        log_admin_action(request, f"tuk_world.restaurant.{action}", f"id={model.id}")

    async def on_model_delete(self, model: Any, request: Request) -> None:
        """커밋 전에 지울 파일명을 모아 둡니다."""
        with SessionLocal() as db:
            row = db.get(Restaurant, model.id)
            request.state.tuk_world_files = (
                [i.filename for i in row.images] if row else []
            )

    async def after_model_delete(self, model: Any, request: Request) -> None:
        """DB 삭제가 끝난 뒤 파일을 지우고 감사 로그를 남깁니다."""
        for filename in request.state.tuk_world_files:
            remove_image(filename)
        log_admin_action(request, "tuk_world.restaurant.delete", f"id={model.id}")


class RestaurantImageAdmin(ModelView, model=RestaurantImage):
    """식당 이미지 업로드·미리보기·삭제. 수정은 막고 삭제 후 재업로드한다."""

    name = "식당 이미지"
    name_plural = "식당 이미지"
    icon = "fa-solid fa-image"
    can_edit = False
    column_list = [RestaurantImage.id, RestaurantImage.restaurant, "preview"]
    column_details_list = [
        RestaurantImage.id,
        RestaurantImage.restaurant,
        RestaurantImage.content_type,
        RestaurantImage.created_at,
        "preview",
    ]
    column_formatters = {"preview": _preview}
    column_formatters_detail = column_formatters
    form_columns = [RestaurantImage.restaurant]

    async def scaffold_form(self, rules: list[str] | None = None) -> type[Form]:
        """sqladmin에는 form_extra_fields가 없어 업로드 필드를 직접 붙인다."""
        form = await super().scaffold_form(rules)
        form.file = FileField("이미지 (JPEG/PNG/WEBP, 5MB 이하)")
        return form

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """업로드 파일을 REST와 같은 규칙으로 검증·저장합니다."""
        upload = data.pop("file", None)
        if not isinstance(upload, UploadFile):
            raise ValueError("이미지 파일을 선택하세요.")
        raw = await upload.read(Config.TUK_WORLD_MAX_IMAGE_BYTES + 1)
        try:
            mime, ext = validate_image(raw, upload.filename, upload.content_type)
        except ImageValidationError as exc:
            raise ValueError(f"이미지 검증 실패: {escape(str(exc))}") from exc
        model.filename = save_image(raw, ext)
        model.content_type = mime

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """감사 로그."""
        log_admin_action(request, "tuk_world.image.upload", f"id={model.id}")

    async def on_model_delete(self, model: Any, request: Request) -> None:
        """커밋 전에 파일명을 보관합니다."""
        request.state.tuk_world_file = model.filename

    async def after_model_delete(self, model: Any, request: Request) -> None:
        """DB 삭제 후 파일 제거와 감사 로그."""
        remove_image(request.state.tuk_world_file)
        log_admin_action(request, "tuk_world.image.delete", f"id={model.id}")


TukWorldAdmin = [RestaurantAdmin, RestaurantImageAdmin]
