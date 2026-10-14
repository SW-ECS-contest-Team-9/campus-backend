"""node.building_id 추가

실내인데 층을 모르는 노드도 어느 건물인지는 알아야 한다(꾸러미 nodes[].buildingKey: indoor 면 필수).
floor_id 만으로는 층 모름(NULL)일 때 건물을 잃는다.

Revision ID: 0002_node_building
Revises: 0001_map_schema
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_node_building"
down_revision: Union[str, None] = "0001_map_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("node", sa.Column("building_id", sa.BigInteger, sa.ForeignKey("building.id", ondelete="CASCADE")))
    op.create_check_constraint(
        "node_location_building",
        "node",
        "(location = 'outdoor' AND building_id IS NULL) OR (location = 'indoor' AND building_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("node_location_building", "node", type_="check")
    op.drop_column("node", "building_id")
