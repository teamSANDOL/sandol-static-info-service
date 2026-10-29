"""관리자 감사 기록: 로그 한 줄 + admin_audit_log 행(추가 전용)."""

import json
from functools import partial
from typing import Any

import anyio

from app.config import logger
from app.database import SessionLocal
from app.models.admin_audit import AdminAuditLog


def record_audit(
    sub: str | None,
    action: str,
    target: str,
    *,
    actor_name: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """감사 로그를 남깁니다. DB 기록이 실패해도 예외를 올리지 않습니다.

    원 작업의 트랜잭션과 섞이지 않도록 독립 세션으로 커밋합니다.

    Args:
        sub: 수행자 Keycloak sub.
        action: 작업 이름.
        target: 대상 식별자.
        actor_name: 수행자 표시 이름(알 수 있을 때만).
        detail: 대상 식별자와 짧은 요약. 변경 전후 전체 내용은 넣지 않는다.
    """
    # target엔 place 등 사용자 입력이 섞여 줄바꿈·따옴표를 이스케이프해 로그 위조를 막는다
    # (sub는 서명 검증된 토큰 값).
    logger.info(
        "admin_audit sub=%s action=%s target=%s",
        sub,
        action,
        json.dumps(target, ensure_ascii=False),
    )
    try:
        with SessionLocal() as db:
            db.add(
                AdminAuditLog(
                    actor_sub=sub,
                    actor_name=actor_name,
                    action=action,
                    target=target[:500],
                    detail=(
                        json.dumps(detail, ensure_ascii=False, default=str)
                        if detail
                        else None
                    ),
                )
            )
            db.commit()
    except Exception:  # 감사 DB 장애가 원 작업을 실패시키면 안 된다.
        logger.error(
            "admin_audit DB 기록 실패 action=%s target=%s",
            action,
            json.dumps(target, ensure_ascii=False),
            exc_info=True,
        )


async def arecord_audit(
    sub: str | None,
    action: str,
    target: str,
    *,
    actor_name: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """record_audit을 스레드로 넘겨 실행합니다. async 핸들러에서 쓰세요.

    동기 SQLite 커밋이 이벤트 루프를 막지 않게 한다. 인자는 record_audit과 같다.

    Args:
        sub: 수행자 Keycloak sub.
        action: `영역.대상.동작` 형식의 작업 이름.
        target: 대상 식별자.
        actor_name: 수행자 표시 이름(알 수 있을 때만).
        detail: 대상 식별자와 짧은 요약.
    """
    await anyio.to_thread.run_sync(
        partial(record_audit, sub, action, target, actor_name=actor_name, detail=detail)
    )
