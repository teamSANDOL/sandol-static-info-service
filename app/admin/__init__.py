"""관리자 화면(sqladmin) 마운트.

설정이 하나라도 빠지면 마운트하지 않는다(fail-closed). 그러면 /admin 과 콜백 모두 404다.
각 뷰는 자기 모듈에서 아래 _VIEWS 이름으로 export하면 자동 등록된다.
모듈이 아직 없으면 건너뛰고, 모듈 안의 import 오류는 그대로 올려 보낸다.
"""

import importlib
import os

from fastapi import FastAPI
from sqladmin import Admin

from app.admin.auth import KeycloakAdminAuth, admin_configured, router
from app.config import Config, logger
from app.database import engine

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

# (모듈, export 이름). export는 뷰 클래스 하나 또는 클래스 리스트.
_VIEWS = [
    ("app.admin.shuttle_view", "ShuttleAdminView"),
    ("app.admin.organization_view", "OrganizationAdminView"),
    ("app.admin.tuk_world_view", "TukWorldAdmin"),
    ("app.admin.audit_view", "AuditLogAdmin"),
]


def _load_views() -> list[type]:
    """_VIEWS에 등록된 관리자 뷰 클래스를 import해 모읍니다.

    모듈 자체가 없으면 건너뛰고, 모듈 안의 import 오류는 그대로 올립니다.

    Returns:
        등록할 뷰 클래스 목록.

    Raises:
        ModuleNotFoundError: 뷰 모듈이 아니라 그 안에서 import한 모듈이 없을 때.
    """
    views: list[type] = []
    for module_name, attr in _VIEWS:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        exported = getattr(module, attr)
        views.extend(exported if isinstance(exported, (list, tuple)) else [exported])
    return views


def mount_admin(app: FastAPI) -> Admin | None:
    """설정이 갖춰졌을 때만 /admin 과 /admin-oauth/callback 을 붙입니다."""
    if not admin_configured():
        logger.warning("관리자 화면을 마운트하지 않음: 관리자 env 설정 누락")
        return None
    app.include_router(router)
    admin = Admin(
        app=app,
        engine=engine,
        authentication_backend=KeycloakAdminAuth(),
        title="산돌이 static-info 관리자",
        templates_dir=TEMPLATES_DIR,
    )
    for view in _load_views():
        admin.add_view(view)
    logger.info("관리자 화면 마운트: %s/admin", Config.ROOT_PATH)
    return admin
