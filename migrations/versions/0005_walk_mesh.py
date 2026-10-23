"""walk_mesh 추가

보행 그물(아3): 걷는 면·길 띠마다 격자 항목 하나(원점·축·칸 크기·칸 수·세모 비트·칸 등급).
꾸러미 walkMeshes 에 그대로 싣는다(entry JSONB). 서버·앱이 이 항목으로 같은 그물을 다시 만든다.

Revision ID: 0005_walk_mesh
Revises: 0004_walk_surface
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_walk_mesh"
down_revision: Union[str, None] = "0004_walk_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "walk_mesh",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("release_id", sa.Integer, sa.ForeignKey("map_release.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("entry", postgresql.JSONB, nullable=False),
        sa.UniqueConstraint("release_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("walk_mesh")
