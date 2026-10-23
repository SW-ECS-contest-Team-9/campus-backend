"""map_release.walk_mesh_classes 추가

보행 그물 칸 등급을 그물마다 두지 않고 릴리스마다 공용 표 하나로 싣는다(꾸러미 walkMeshClasses).
walk_mesh.entry 의 cls 번호는 이 표의 번호다.

Revision ID: 0006_walk_mesh_classes
Revises: 0005_walk_mesh
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_walk_mesh_classes"
down_revision: Union[str, None] = "0005_walk_mesh"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("map_release", sa.Column("walk_mesh_classes", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("map_release", "walk_mesh_classes")
