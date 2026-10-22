"""edge.indicators, node.indicators 추가

경로 지표(기울기·계단 단 수·지붕·조명·차도 노출·휠체어 판정·걷는 시간 등)를 가져올 때 계산해 둔다.
값이 늘 바뀔 수 있는 선택 칸이라 칸마다 열을 만들지 않고 JSONB 하나에 담는다. NULL = 계산 안 함(옛 릴리스).

Revision ID: 0003_route_indicators
Revises: 0002_node_building
Create Date: 2026-10-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_route_indicators"
down_revision: Union[str, None] = "0002_node_building"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("edge", sa.Column("indicators", postgresql.JSONB))
    op.add_column("node", sa.Column("indicators", postgresql.JSONB))


def downgrade() -> None:
    op.drop_column("node", "indicators")
    op.drop_column("edge", "indicators")
