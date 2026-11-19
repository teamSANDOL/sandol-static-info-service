"""감사 로그 조회 전용 화면. 생성·수정·삭제는 모두 막는다."""

from sqladmin import ModelView

from app.admin.auth import FullAdminOnly
from app.models.admin_audit import AdminAuditLog


class AuditLogAdmin(FullAdminOnly, ModelView, model=AdminAuditLog):
    """관리자 감사 로그(읽기 전용, 최신순)."""

    name = "감사 로그"
    name_plural = "감사 로그"
    icon = "fa-solid fa-clipboard-list"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        AdminAuditLog.id,
        AdminAuditLog.at,
        AdminAuditLog.actor_sub,
        AdminAuditLog.action,
        AdminAuditLog.target,
    ]
    column_details_list = [*column_list, AdminAuditLog.actor_name, AdminAuditLog.detail]
    column_searchable_list = [
        AdminAuditLog.action,
        AdminAuditLog.actor_sub,
        AdminAuditLog.actor_name,
    ]
    column_default_sort = [(AdminAuditLog.id, True)]
