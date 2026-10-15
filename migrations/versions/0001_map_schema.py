"""지도 릴리스와 길 그래프 표

ERD: 볼트 백엔드/작업로그/2026-10-14-ERD-초안 (수정 1~3 반영)

Revision ID: 0001_map_schema
Revises:
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_map_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOCATIONS = "'outdoor', 'indoor'"
ACCESS = "'allowed', 'prohibited', 'restricted', 'unknown'"


def release_fk() -> sa.Column:
    return sa.Column("release_id", sa.Integer, sa.ForeignKey("map_release.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "campus",
        sa.Column("id", sa.SmallInteger, sa.Identity(), primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("crs", sa.Integer, nullable=False, server_default="5186"),
        sa.Column("vertical_datum", sa.Text, nullable=False, server_default="KVD_INCHEON_MSL"),
    )

    op.create_table(
        "map_release",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("campus_id", sa.SmallInteger, sa.ForeignKey("campus.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("file_path", sa.Text),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("sha256", sa.CHAR(64)),
        sa.Column("min_app_version", sa.Text),
        sa.Column("source", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("campus_id", "version"),
        sa.CheckConstraint("status IN ('draft', 'published', 'withdrawn')", name="map_release_status"),
        sa.CheckConstraint(
            "status <> 'published' OR (file_path IS NOT NULL AND size_bytes IS NOT NULL"
            " AND sha256 IS NOT NULL AND published_at IS NOT NULL)",
            name="map_release_published_has_file",
        ),
    )

    op.create_table(
        "building",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        release_fk(),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("ground_floors", sa.SmallInteger),
        sa.Column("underground_floors", sa.SmallInteger),
        sa.Column("center_x", sa.Numeric(10, 2)),
        sa.Column("center_y", sa.Numeric(10, 2)),
        sa.Column("footprint", postgresql.JSONB),
        sa.UniqueConstraint("release_id", "key"),
        sa.CheckConstraint("ground_floors >= 0 AND underground_floors >= 0", name="building_floor_counts"),
    )

    op.create_table(
        "floor",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        release_fk(),
        sa.Column("building_id", sa.BigInteger, sa.ForeignKey("building.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("sort_order", sa.SmallInteger, nullable=False),
        sa.Column("floor_z_m", sa.Numeric(7, 2)),
        sa.Column("z_estimated", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("release_id", "key"),
        sa.UniqueConstraint("building_id", "code"),
        sa.UniqueConstraint("building_id", "sort_order"),
    )

    op.create_table(
        "node",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        release_fk(),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("floor_id", sa.BigInteger, sa.ForeignKey("floor.id", ondelete="CASCADE")),
        sa.Column("kind", sa.Text, nullable=False, server_default="junction"),
        sa.Column("x", sa.Numeric(10, 2), nullable=False),
        sa.Column("y", sa.Numeric(10, 2), nullable=False),
        sa.Column("z", sa.Numeric(7, 2), nullable=False),
        sa.Column("estimated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("release_id", "key"),
        sa.CheckConstraint(f"location IN ({LOCATIONS})", name="node_location"),
        sa.CheckConstraint("location = 'indoor' OR floor_id IS NULL", name="node_outdoor_has_no_floor"),
        sa.CheckConstraint(
            "kind IN ('junction', 'door', 'stair_end', 'elevator_stop', 'end', 'other')", name="node_kind"
        ),
    )
    op.create_index("node_floor", "node", ["floor_id"])

    op.create_table(
        "edge",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        release_fk(),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("from_node_id", sa.BigInteger, sa.ForeignKey("node.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_node_id", sa.BigInteger, sa.ForeignKey("node.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False, server_default="both"),
        sa.Column("pedestrian_access", sa.Text, nullable=False, server_default="allowed"),
        sa.Column("wheelchair_access", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("length_m", sa.Numeric(8, 2), nullable=False),
        sa.Column("step_count", sa.SmallInteger),
        sa.Column("slope_pct", sa.Numeric(5, 2)),
        sa.Column("width_m", sa.Numeric(5, 2)),
        sa.Column("geometry", postgresql.JSONB, nullable=False),
        sa.Column("estimated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("release_id", "key"),
        sa.CheckConstraint("from_node_id <> to_node_id", name="edge_not_loop"),
        sa.CheckConstraint("length_m > 0", name="edge_length_positive"),
        sa.CheckConstraint("step_count >= 0", name="edge_step_count"),
        sa.CheckConstraint("width_m > 0", name="edge_width_positive"),
        sa.CheckConstraint(
            "kind IN ('walkway', 'sidewalk', 'crossing', 'corridor', 'stairs', 'ramp', 'elevator', 'vehicle_road')",
            name="edge_kind",
        ),
        sa.CheckConstraint("direction IN ('both', 'forward', 'backward', 'unknown')", name="edge_direction"),
        sa.CheckConstraint(f"pedestrian_access IN ({ACCESS})", name="edge_pedestrian_access"),
        sa.CheckConstraint(f"wheelchair_access IN ({ACCESS})", name="edge_wheelchair_access"),
    )
    op.create_index("edge_from_node", "edge", ["from_node_id"])
    op.create_index("edge_to_node", "edge", ["to_node_id"])
    # 같은 두 점 사이 같은 종류 길이 방향만 바꿔 두 번 들어오는 것을 막는다(계산식 UNIQUE)
    op.execute(
        "CREATE UNIQUE INDEX edge_same_pair_kind ON edge"
        " (release_id, LEAST(from_node_id, to_node_id), GREATEST(from_node_id, to_node_id), kind)"
    )

    op.create_table(
        "place",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        release_fk(),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("building_id", sa.BigInteger, sa.ForeignKey("building.id", ondelete="CASCADE")),
        sa.Column("floor_id", sa.BigInteger, sa.ForeignKey("floor.id", ondelete="CASCADE")),
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("node.id", ondelete="SET NULL")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("x", sa.Numeric(10, 2), nullable=False),
        sa.Column("y", sa.Numeric(10, 2), nullable=False),
        sa.Column("z", sa.Numeric(7, 2)),
        sa.UniqueConstraint("release_id", "key"),
        sa.CheckConstraint(f"location IN ({LOCATIONS})", name="place_location"),
        sa.CheckConstraint(
            "(location = 'outdoor' AND building_id IS NULL AND floor_id IS NULL)"
            " OR (location = 'indoor' AND building_id IS NOT NULL)",
            name="place_location_building",
        ),
        sa.CheckConstraint(
            "category IN ('entrance', 'facility', 'landmark', 'parking', 'bus_stop', 'room', 'other')",
            name="place_category",
        ),
    )
    op.create_index("place_release", "place", ["release_id"])

    op.create_table(
        "walk_area",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        release_fk(),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("floor_id", sa.BigInteger, sa.ForeignKey("floor.id", ondelete="CASCADE")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("polygon", postgresql.JSONB, nullable=False),
        sa.Column("z_m", sa.Numeric(7, 2)),
        sa.Column("estimated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("release_id", "key"),
        sa.CheckConstraint(f"location IN ({LOCATIONS})", name="walk_area_location"),
        sa.CheckConstraint("location = 'indoor' OR floor_id IS NULL", name="walk_area_outdoor_has_no_floor"),
        sa.CheckConstraint("kind IN ('field', 'plaza', 'parking_aisle', 'other')", name="walk_area_kind"),
    )

    op.create_table(
        "walk_area_node",
        sa.Column("area_id", sa.BigInteger, sa.ForeignKey("walk_area.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("node_id", sa.BigInteger, sa.ForeignKey("node.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    for table in ("walk_area_node", "walk_area", "place", "edge", "node", "floor", "building", "map_release", "campus"):
        op.drop_table(table)
