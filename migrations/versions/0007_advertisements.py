"""광고 조회용 테이블 추가."""

import sqlalchemy as sa
from alembic import op

revision = "0007_advertisements"
down_revision = "0006_walk_mesh_classes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advertisements",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("starts_at < ends_at", name="advertisements_valid_period"),
        sa.CheckConstraint("length(trim(title)) > 0", name="advertisements_title_not_empty"),
    )


def downgrade() -> None:
    op.drop_table("advertisements")
