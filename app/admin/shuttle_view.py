"""셔틀 시간표 관리자 화면(sqladmin BaseView).

place(출처 문서)별 시간표 목록, 직접 등록·편집·삭제, PDF 동기화를 한 화면 경로에서 처리한다.
라우트를 하나(GET/POST /shuttle)로 둔 이유: BaseView는 expose마다 identity를 덮어써서
메뉴 링크가 마지막 등록 함수로 바뀐다. 쓰기는 POST의 action 필드로 나눈다.
"""

import re
from typing import get_args
from urllib.parse import urlencode

from pydantic import ValidationError
from sqladmin import BaseView, expose
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.admin.auth import admin_sub, csrf_token, log_admin_action, verify_csrf
from app.schemas.shuttle import DayType, Direction, ParsedTrip
from app.utils.shuttle_timetable import (
    delete_timetable,
    load_store,
    save_user_timetable,
    sync_shuttle_timetables,
)

_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
_MD = re.compile(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
_FIELDS = (
    "direction",
    "day_type",
    "day_note",
    "departure_time",
    "departure_time_end",
    "valid_from",
    "valid_to",
    "period_note",
    "boarding_place",
    "note",
)
_BLANK_ROWS = 5


def _opt(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def form_rows(form: FormData) -> list[dict]:
    """편집 폼의 행(t-{i}-필드)을 원본 dict로 모읍니다. 빈 행·삭제 체크 행은 뺍니다."""
    count = str(form.get("rows") or "")
    rows = []
    for i in range(min(int(count), 2000) if count.isdigit() else 0):
        prefix = f"t-{i}-"
        row = {k: _opt(form.get(prefix + k)) for k in _FIELDS}
        if row["departure_time"] is not None and not form.get(prefix + "delete"):
            rows.append(row)
    return rows


def parse_trips(rows: list[dict]) -> list[ParsedTrip]:
    """form_rows 결과를 검증해 ParsedTrip 목록으로 바꿉니다.

    Raises:
        ValueError: 시각·기간 형식이 틀리거나 방향·운행일이 허용값이 아닐 때.
    """
    trips = []
    for n, row in enumerate(rows, 1):
        for key in ("departure_time", "departure_time_end"):
            if row[key] and not _TIME.fullmatch(row[key]):
                raise ValueError(f"{n}번째 행: 시각은 HH:MM 형식이어야 합니다.")
        for key in ("valid_from", "valid_to"):
            if row[key] and not _MD.fullmatch(row[key]):
                raise ValueError(f"{n}번째 행: 운행 기간은 MM-DD 형식이어야 합니다.")
        try:
            trips.append(ParsedTrip(**row))
        except ValidationError as exc:
            raise ValueError(
                f"{n}번째 행: 방향 또는 운행일 값이 올바르지 않습니다."
            ) from exc
    return trips


class ShuttleAdminView(BaseView):
    """셔틀 시간표 관리. 수동 저장본(source=user)은 force 없는 동기화가 덮어쓰지 않는다."""

    name = "셔틀 시간표"
    icon = "fa-solid fa-bus"

    def _back(self, request: Request, **params: str) -> Response:
        # 게이트웨이가 X-Forwarded-Proto를 넘기지 않으므로 절대 URL 대신 경로로 보낸다.
        path = request.url_for("admin:shuttle").path
        query = f"?{urlencode(params)}" if params else ""
        return RedirectResponse(path + query, status_code=303)

    async def _render(
        self, request: Request, template: str, status_code: int = 200, **ctx: object
    ) -> Response:
        response = await self.templates.TemplateResponse(
            request,
            template,
            {
                "base": request.url_for("admin:shuttle").path,
                "csrf_token": csrf_token(request),
                "msg": request.query_params.get("msg"),
                **ctx,
            },
        )
        response.status_code = status_code
        return response

    async def _edit_page(
        self,
        request: Request,
        doc: dict,
        is_new: bool,
        error: str | None = None,
    ) -> Response:
        """doc은 place·title·trips를 가진 시간표 문서(또는 폼에서 다시 모은 값)."""
        extra = request.query_params.get("extra", "")
        blanks = int(extra) if extra.isdigit() else _BLANK_ROWS
        return await self._render(
            request,
            "admin/shuttle_edit.html",
            400 if error else 200,
            place=doc.get("place") or "",
            is_new=is_new,
            doc_title=doc.get("title"),
            trips=doc.get("trips") or [],
            blanks=min(blanks, 100),
            directions=get_args(Direction),
            day_types=get_args(DayType),
            error=error,
        )

    @expose("/shuttle", methods=["GET", "POST"], identity="shuttle")
    async def shuttle(self, request: Request) -> Response:
        """GET: 목록 또는 편집 폼(?place=, ?new=1). POST: action=save|delete|sync."""
        if request.method == "POST":
            return await self._post(request)
        store = load_store()
        place = request.query_params.get("place")
        if request.query_params.get("new"):
            return await self._edit_page(request, {}, True)
        if place is not None:
            doc = store.get(place)
            if doc is None:
                return self._back(request, msg=f"없는 시간표입니다: {place}")
            return await self._edit_page(request, doc, False)
        docs = sorted(store.values(), key=lambda d: d.get("place") or "")
        return await self._render(request, "admin/shuttle_list.html", docs=docs)

    async def _post(self, request: Request) -> Response:
        form = await request.form()
        if not verify_csrf(request, _opt(form.get("csrf_token"))):
            return Response("CSRF 토큰이 올바르지 않습니다.", status_code=403)
        handler = {
            "sync": self._sync,
            "delete": self._delete,
            "save": self._save,
        }.get(str(form.get("action")))
        if handler is None:
            return Response("알 수 없는 요청입니다.", status_code=400)
        return await handler(request, form)

    async def _sync(self, request: Request, form: FormData) -> Response:
        force = bool(form.get("force"))
        log_admin_action(request, "shuttle_sync", f"force={force}")
        updated = await sync_shuttle_timetables(force)
        msg = (
            f"동기화 완료: {', '.join(updated)}"
            if updated
            else "변경 없음(같은 PDF·수동 저장본) 또는 실패. 실패면 서버 로그를 확인하세요."
        )
        return self._back(request, msg=msg)

    async def _delete(self, request: Request, form: FormData) -> Response:
        place = _opt(form.get("place")) or ""
        if not place or not await delete_timetable(place):
            return self._back(request, msg=f"없는 시간표입니다: {place}")
        log_admin_action(request, "shuttle_delete", place)
        return self._back(request, msg=f"삭제했습니다: {place}")

    async def _save(self, request: Request, form: FormData) -> Response:
        place = _opt(form.get("place")) or ""
        is_new = bool(form.get("is_new"))
        title = _opt(form.get("title"))
        rows = form_rows(form)
        try:
            if not place:
                raise ValueError("시간표 이름(place)을 입력하세요.")
            if is_new and place in load_store():
                raise ValueError(
                    "같은 이름의 시간표가 이미 있습니다. 목록에서 편집하세요."
                )
            trips = parse_trips(rows)
            if not trips:
                raise ValueError("출발 시각이 있는 행이 하나 이상 필요합니다.")
        except ValueError as exc:
            submitted = {"place": place, "title": title, "trips": rows}
            return await self._edit_page(request, submitted, is_new, str(exc))
        # ponytail: 동시 편집은 마지막 저장이 이긴다. 충돌이 문제가 되면 updated_at 낙관적 잠금 추가.
        await save_user_timetable(place, trips, title, updated_by=admin_sub(request))
        log_admin_action(request, "shuttle_save", place)
        return self._back(request, msg=f"저장했습니다: {place}")
