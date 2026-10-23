"""walk_surface, walk_surface_node 추가

기단 면(트래커 구조물 platform 을 사람이 다니는 곳 외곽으로 자른 것). 기울어진 면도 담는다:
top = {"z": 높이} 또는 {"profile": {origin, direction, points}}, polygons = [[바깥 고리, 구멍 고리...], ...].
경로 계산(서버·앱)은 같은 면 위 노드끼리 면 안에 머무는 직선으로 잇는다.

Revision ID: 0004_walk_surface
Revises: 0003_route_indicators
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_walk_surface"
down_revision: Union[str, None] = "0003_route_indicators"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "walk_surface",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("release_id", sa.Integer, sa.ForeignKey("map_release.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("material", sa.Text),
        sa.Column("top", postgresql.JSONB, nullable=False),
        sa.Column("polygons", postgresql.JSONB, nullable=False),
        sa.Column("platforms", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("estimated", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("release_id", "key"),
    )
    op.create_table(
        "walk_surface_node",
        sa.Column("surface_id", sa.BigInteger, sa.ForeignKey("walk_surface.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("node.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("walk_surface_node")
    op.drop_table("walk_surface")
