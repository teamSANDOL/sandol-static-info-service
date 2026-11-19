"""기준 스키마: TASK-94 시점의 TUK World 테이블.

예전 배포는 alembic 없이 create_all로 테이블을 만들었으므로, 이미 있는 테이블은
만들지 않는다(멱등).

Revision ID: 0001
Revises:
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """restaurants·restaurant_images 테이블을 없을 때만 만든다."""
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "tuk_world_restaurant" not in existing:
        op.create_table(
            "tuk_world_restaurant",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("naver_map_url", sa.String(500)),
            sa.Column("kakao_map_url", sa.String(500)),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column("discount_info", sa.Text()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "tuk_world_restaurant_image" not in existing:
        op.create_table(
            "tuk_world_restaurant_image",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "restaurant_id",
                sa.Integer(),
                sa.ForeignKey("tuk_world_restaurant.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("filename", sa.String(64), nullable=False),
            sa.Column("content_type", sa.String(32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("filename"),
        )
        op.create_index(
            "ix_tuk_world_restaurant_image_restaurant_id",
            "tuk_world_restaurant_image",
            ["restaurant_id"],
        )


def downgrade() -> None:
    """두 테이블을 지운다."""
    op.drop_table("tuk_world_restaurant_image")
    op.drop_table("tuk_world_restaurant")
