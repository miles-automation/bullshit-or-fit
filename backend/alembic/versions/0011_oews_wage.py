"""jobtrends: OEWS location wage bands (the long-tail, location-real comp signal).

BLS OEWS software-developer wage percentiles per state (+ national), sourced via
O*NET (bls.gov itself bot-blocks). This is the ground-truth "what does this pay
where you actually are" that the head-heavy live-role feeds (HN/ATS) can't give.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "oews_wage",
        sa.Column("soc", sa.Text(), nullable=False),
        sa.Column("occupation", sa.Text(), nullable=False),
        sa.Column("area_type", sa.Text(), nullable=False),  # 'national' | 'state'
        sa.Column("area_code", sa.Text(), nullable=False),  # 'US' | 'WY' | ...
        sa.Column("area_name", sa.Text(), nullable=False),
        sa.Column("p10_usd", sa.Integer(), nullable=False),
        sa.Column("p25_usd", sa.Integer(), nullable=False),
        sa.Column("median_usd", sa.Integer(), nullable=False),
        sa.Column("p75_usd", sa.Integer(), nullable=False),
        sa.Column("p90_usd", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("soc", "area_code"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("oews_wage", schema=SCHEMA)
