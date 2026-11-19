"""TASK-104: 분류·주소·수정자 컬럼과 관리자 감사 로그 테이블.

예전 배포는 앱이 ADD COLUMN/create_all로 이미 추가했을 수 있으므로 각 항목을
inspector로 확인해 없는 것만 만든다(멱등).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESTAURANT = "tuk_world_restaurant"
IMAGE = "tuk_world_restaurant_image"
AUDIT = "admin_audit_log"


def _add_column(table: str, column: sa.Column) -> None:
    """컬럼이 없을 때만 추가한다."""
    names = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def _create_index(name: str, table: str, columns: list[str]) -> None:
    """인덱스가 없을 때만 만든다."""
    names = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in names:
        op.create_index(name, table, columns)


def upgrade() -> None:
    """category·address·updated_by 컬럼과 admin_audit_log 테이블을 추가한다."""
    _add_column(RESTAURANT, sa.Column("category", sa.String(20)))
    _add_column(RESTAURANT, sa.Column("address", sa.String(200)))
    _add_column(RESTAURANT, sa.Column("updated_by", sa.String(100)))
    _add_column(IMAGE, sa.Column("updated_by", sa.String(100)))
    _create_index("ix_tuk_world_restaurant_category", RESTAURANT, ["category"])

    if AUDIT not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            AUDIT,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("actor_sub", sa.String(100)),
            sa.Column("actor_name", sa.String(100)),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("target", sa.String(500), nullable=False),
            sa.Column("detail", sa.Text()),
        )
    _create_index("ix_admin_audit_log_at", AUDIT, ["at"])
    _create_index("ix_admin_audit_log_actor_sub", AUDIT, ["actor_sub"])
    _create_index("ix_admin_audit_log_action", AUDIT, ["action"])


def downgrade() -> None:
    """추가한 테이블과 컬럼을 되돌린다."""
    op.drop_table(AUDIT)
    op.drop_index("ix_tuk_world_restaurant_category", table_name=RESTAURANT)
    with op.batch_alter_table(IMAGE) as batch:
        batch.drop_column("updated_by")
    with op.batch_alter_table(RESTAURANT) as batch:
        batch.drop_column("updated_by")
        batch.drop_column("address")
        batch.drop_column("category")
