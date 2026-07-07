"""jobtrends: derived post_comp + cohort_month tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-06
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "post_comp",
        sa.Column("hn_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("min_amount", sa.Integer(), nullable=False),
        sa.Column("max_amount", sa.Integer(), nullable=False),
        sa.Column("midpoint", sa.Integer(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("raw_match", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["hn_id"], [f"{SCHEMA}.hn_hiring_posts.hn_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("hn_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_post_comp_month", "post_comp", ["month"], schema=SCHEMA)

    op.create_table(
        "cohort_month",
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("active_authors", sa.Integer(), nullable=False),
        sa.Column("new_authors", sa.Integer(), nullable=False),
        sa.Column("returning_authors", sa.Integer(), nullable=False),
        sa.Column("churned_prev", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("month"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("cohort_month", schema=SCHEMA)
    op.drop_index("ix_post_comp_month", table_name="post_comp", schema=SCHEMA)
    op.drop_table("post_comp", schema=SCHEMA)
