"""TUK World 관리 화면: 식당 CRUD와 이미지 업로드·삭제.

이미지 검증·저장·삭제는 REST API와 같은 app.utils.tuk_world_images를 쓴다.
작성자는 updated_by 컬럼과 감사 로그(admin_audit_log)에 남긴다.
"""

from typing import Any

import anyio
from markupsafe import Markup, escape
from sqladmin import ModelView
from sqladmin.filters import AllUniqueStringValuesFilter
from starlette.datastructures import UploadFile
from starlette.requests import Request
from wtforms import FileField, FloatField, Form
from wtforms.validators import NumberRange, Optional, Regexp

from app.admin.auth import FullAdminOnly, admin_sub, log_admin_action
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
    """이미지 한 장의 썸네일 `<img>` 태그를 만듭니다.

    Args:
        image_id: 이미지 id.

    Returns:
        이스케이프된 HTML 조각.
    """
    src = f"{Config.ROOT_PATH}/tuk-world/images/{image_id}"
    return Markup('<img src="{}" style="max-height:80px;margin:2px">').format(src)


def _thumbs(model: Any, attr: Any) -> Markup:
    """식당의 모든 이미지 썸네일을 이어 붙입니다(sqladmin 컬럼 포매터).

    Args:
        model: 식당 모델.
        attr: 컬럼 이름(사용하지 않음).

    Returns:
        썸네일 HTML 조각.
    """
    return Markup("").join(_thumb(i.id) for i in model.images)


def _preview(model: Any, attr: Any) -> Markup:
    """이미지 모델의 미리보기 태그를 만듭니다(sqladmin 컬럼 포매터).

    Args:
        model: 이미지 모델.
        attr: 컬럼 이름(사용하지 않음).

    Returns:
        썸네일 HTML 조각.
    """
    return _thumb(model.id)


class RestaurantAdmin(FullAdminOnly, ModelView, model=Restaurant):
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

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """수정자를 기록합니다.

        Args:
            data: 폼 데이터.
            model: 저장할 식당.
            is_created: 새로 만드는 중이면 True.
            request: 현재 요청.
        """
        model.updated_by = admin_sub(request)

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """커밋 뒤 감사 로그를 남깁니다.

        Args:
            data: 폼 데이터.
            model: 저장된 식당.
            is_created: 새로 만들었으면 True.
            request: 현재 요청.
        """
        action = "create" if is_created else "update"
        await log_admin_action(
            request, f"tuk_world.restaurant.{action}", f"id={model.id}"
        )

    async def on_model_delete(self, model: Any, request: Request) -> None:
        """커밋 전에 지울 파일명을 모아 둡니다.

        Args:
            model: 삭제할 식당.
            request: 현재 요청(파일명 목록을 request.state에 보관한다).
        """
        with SessionLocal() as db:
            row = db.get(Restaurant, model.id)
            request.state.tuk_world_files = (
                [i.filename for i in row.images] if row else []
            )

    async def after_model_delete(self, model: Any, request: Request) -> None:
        """DB 삭제가 끝난 뒤 파일을 지우고 감사 로그를 남깁니다.

        Args:
            model: 삭제된 식당.
            request: 현재 요청.
        """
        for filename in request.state.tuk_world_files:
            remove_image(filename)
        await log_admin_action(request, "tuk_world.restaurant.delete", f"id={model.id}")


class RestaurantImageAdmin(FullAdminOnly, ModelView, model=RestaurantImage):
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
        """업로드 필드를 붙인 폼 클래스를 만듭니다.

        sqladmin에는 form_extra_fields가 없어 직접 붙인다.

        Args:
            rules: 폼에 넣을 필드 규칙.

        Returns:
            file 필드가 추가된 폼 클래스.
        """
        form = await super().scaffold_form(rules)
        form.file = FileField("이미지 (JPEG/PNG/WEBP, 5MB 이하)")
        return form

    async def on_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """업로드 파일을 REST와 같은 규칙으로 검증·저장합니다.

        sqladmin이 이 훅을 이벤트 루프에서 실행하므로 Pillow 검증과 파일 쓰기는 스레드로 넘긴다.

        Args:
            data: 폼 데이터(file 항목을 꺼내 쓴다).
            model: 저장할 이미지.
            is_created: 새로 만드는 중이면 True.
            request: 현재 요청.

        Raises:
            ValueError: 파일이 없거나 이미지 검증에 실패했을 때.
        """
        upload = data.pop("file", None)
        if not isinstance(upload, UploadFile):
            raise ValueError("이미지 파일을 선택하세요.")
        raw = await upload.read(Config.TUK_WORLD_MAX_IMAGE_BYTES + 1)
        # sqladmin이 이 훅을 이벤트 루프에서 실행하므로 Pillow 검증과 파일 쓰기는
        # 스레드로 넘긴다.
        try:
            mime, ext = await anyio.to_thread.run_sync(
                validate_image, raw, upload.filename, upload.content_type
            )
        except ImageValidationError as exc:
            raise ValueError(f"이미지 검증 실패: {escape(str(exc))}") from exc
        model.filename = await anyio.to_thread.run_sync(save_image, raw, ext)
        request.state.tuk_world_new_file = model.filename
        model.content_type = mime
        model.updated_by = admin_sub(request)

    async def insert_model(self, request: Request, data: dict) -> Any:
        """이미지를 저장합니다. 커밋이 실패하면 저장해 둔 파일을 지웁니다.

        sqladmin에는 커밋 실패 훅이 없어 여기서 고아 파일을 정리한다.

        Args:
            request: 현재 요청.
            data: 폼 데이터.

        Returns:
            저장된 이미지 모델.

        Raises:
            Exception: 저장 중 발생한 예외를 파일 정리 후 그대로 다시 올린다.
        """
        try:
            return await super().insert_model(request, data)
        except Exception:
            filename = getattr(request.state, "tuk_world_new_file", None)
            if filename:
                await anyio.to_thread.run_sync(remove_image, filename)
            raise

    async def after_model_change(
        self, data: dict, model: Any, is_created: bool, request: Request
    ) -> None:
        """커밋이 끝났으므로 정리 대상에서 빼고 감사 로그를 남깁니다.

        Args:
            data: 폼 데이터.
            model: 저장된 이미지.
            is_created: 새로 만들었으면 True.
            request: 현재 요청.
        """
        request.state.tuk_world_new_file = None
        await log_admin_action(request, "tuk_world.image.upload", f"id={model.id}")

    async def on_model_delete(self, model: Any, request: Request) -> None:
        """커밋 전에 파일명을 보관합니다.

        Args:
            model: 삭제할 이미지.
            request: 현재 요청(파일명을 request.state에 보관한다).
        """
        request.state.tuk_world_file = model.filename

    async def after_model_delete(self, model: Any, request: Request) -> None:
        """DB 삭제 후 파일 제거와 감사 로그.

        Args:
            model: 삭제된 이미지.
            request: 현재 요청.
        """
        remove_image(request.state.tuk_world_file)
        await log_admin_action(request, "tuk_world.image.delete", f"id={model.id}")


TukWorldAdmin = [RestaurantAdmin, RestaurantImageAdmin]
