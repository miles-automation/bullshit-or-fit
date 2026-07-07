"""jobtrends: derived keyword_source_demand (cross-source skill unification).

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "keyword_source_demand",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("roles_matched", sa.Integer(), nullable=False),
        sa.Column("roles_total", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "keyword"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("keyword_source_demand", schema=SCHEMA)
