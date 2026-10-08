"""Initial schema for geospatial file measurement API.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-10-08 20:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. files table
    op.create_table(
        "files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("format", sa.String(length=32), nullable=False),
        sa.Column("source_crs", sa.String(length=64), nullable=True),
        sa.Column("measurement_crs", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("feature_count", sa.Integer(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. features table
    op.create_table(
        "features",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("feature_index", sa.Integer(), nullable=False),
        sa.Column("geometry_type", sa.String(length=64), nullable=False),
        sa.Column("geometry_valid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("geometry_empty", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("validation_message", sa.Text(), nullable=True),
        sa.Column("geometry_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("properties_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", "feature_index", name="uq_file_feature_idx"),
    )

    # 3. measurements table
    op.create_table(
        "measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=False),
        sa.Column("measurement_type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("measurement_crs", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature_id"),
    )

    # 4. processing_errors table
    op.create_table(
        "processing_errors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. insight_reports table
    op.create_table(
        "insight_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("result_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("insight_reports")
    op.drop_table("processing_errors")
    op.drop_table("measurements")
    op.drop_table("features")
    op.drop_table("files")
