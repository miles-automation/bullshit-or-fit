"""jobtrends: derived keyword_month_stats table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-06
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "keyword_month_stats",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("posts_matched", sa.Integer(), nullable=False),
        sa.Column("posts_total", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("month", "keyword"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_keyword_month_stats_keyword", "keyword_month_stats", ["keyword"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index(
        "ix_keyword_month_stats_keyword", table_name="keyword_month_stats", schema=SCHEMA
    )
    op.drop_table("keyword_month_stats", schema=SCHEMA)
