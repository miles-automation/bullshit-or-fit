"""jobtrends: commute-shed employer registry (the Laramie-reachable map).

A curated reference table of employers reachable from a home base — tiered by
reachability (in-town / ~50 mi / front-range / WY-remote). Seeded from
`commute_shed.SEED_EMPLOYERS` each tick; live open-role counts join back from
`ats_jobs` (source='commute_shed') by ats_token.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-08
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "commute_shed_employer",
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # 'laramie' | 'cheyenne' | 'front_range' | 'wy_remote'
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("hq_city", sa.Text(), nullable=True),
        sa.Column("hq_state", sa.Text(), nullable=True),
        sa.Column("distance_mi", sa.Integer(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),  # greenhouse|lever|ashby
        sa.Column("ats_token", sa.Text(), nullable=True),
        sa.Column("careers_url", sa.Text(), nullable=False),
        sa.Column(
            "engineer_relevant",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("token"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_commute_shed_employer_tier",
        "commute_shed_employer",
        ["tier"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_commute_shed_employer_tier",
        table_name="commute_shed_employer",
        schema=SCHEMA,
    )
    op.drop_table("commute_shed_employer", schema=SCHEMA)
