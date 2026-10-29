"""조직도(school_info.json) 보기·검증·편집 화면."""

from typing import Any

from sqladmin import BaseView, expose
from starlette.requests import Request
from starlette.responses import Response

from app.admin.auth import admin_sub, csrf_token, log_admin_action, verify_csrf
from app.config import Config, logger
from app.utils.university_structure import (
    parse_school_info,
    read_school_info_meta,
    save_school_info,
)


class OrganizationAdminView(BaseView):
    """조직도 트리 미리보기와 JSON 편집."""

    name = "조직도"
    icon = "fa-solid fa-sitemap"
    identity = "organization"

    async def _render(
        self,
        request: Request,
        text: str | None = None,
        status: int = Config.HttpStatus.OK,
        error: str | None = None,
        message: str | None = None,
    ) -> Response:
        """조직도 편집 화면을 렌더링합니다.

        Args:
            request: 현재 요청.
            text: 편집기에 채울 JSON 원문. None이면 현재 파일을 읽어 보여 준다.
            status: 응답 상태 코드.
            error: 화면에 표시할 오류 메시지.
            message: 화면에 표시할 안내 메시지.

        Returns:
            템플릿 응답.
        """
        if text is None:
            try:
                with open(Config.SCHOOL_INFO_PATH, encoding="utf-8") as f:
                    text = f.read()
            except OSError as exc:
                text, error = "", error or f"조직도 파일을 읽을 수 없습니다: {exc}"
        tree: Any = None
        try:
            tree = parse_school_info(text)[1].root
        except ValueError as exc:
            error = error or str(exc)
        return await self.templates.TemplateResponse(
            request,
            "admin/organization.html",
            {
                "text": text,
                "tree": tree,
                "error": error,
                "message": message,
                "meta": read_school_info_meta(Config.SCHOOL_INFO_PATH),
                "csrf_token": csrf_token(request),
            },
            status_code=status,
        )

    @expose("/organization", methods=["GET"], identity="organization")
    async def page(self, request: Request) -> Response:
        """현재 조직도 보기."""
        return await self._render(request)

    @expose(
        "/organization/validate", methods=["POST"], identity="organization_validate"
    )
    async def validate(self, request: Request) -> Response:
        """저장하지 않고 검증만 합니다."""
        form = await request.form()
        if not verify_csrf(request, str(form.get("csrf_token") or "")):
            return Response("CSRF 검증 실패", status_code=Config.HttpStatus.FORBIDDEN)
        text = str(form.get("content") or "")
        try:
            parse_school_info(text)
        except ValueError as exc:
            return await self._render(
                request, text, Config.HttpStatus.BAD_REQUEST, error=str(exc)
            )
        return await self._render(request, text, message="검증 통과 (저장 전)")

    @expose("/organization/save", methods=["POST"], identity="organization_save")
    async def save(self, request: Request) -> Response:
        """검증 후 원자적으로 저장합니다."""
        form = await request.form()
        if not verify_csrf(request, str(form.get("csrf_token") or "")):
            return Response("CSRF 검증 실패", status_code=Config.HttpStatus.FORBIDDEN)
        text = str(form.get("content") or "")
        try:
            save_school_info(text, Config.SCHOOL_INFO_PATH, admin_sub(request))
        except ValueError as exc:
            return await self._render(
                request, text, Config.HttpStatus.BAD_REQUEST, error=str(exc)
            )
        except OSError as exc:
            logger.error("조직도 저장 실패: %s", exc)
            return await self._render(
                request,
                text,
                Config.HttpStatus.OK,
                error=f"저장하지 못했습니다(쓰기 불가 경로일 수 있음): {exc}",
            )
        await log_admin_action(
            request, "organization.school_info.save", Config.SCHOOL_INFO_PATH
        )
        return await self._render(request, message="저장했습니다.")
