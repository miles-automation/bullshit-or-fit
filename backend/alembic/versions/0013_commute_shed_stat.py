"""jobtrends: commute-shed velocity history (per-employer daily open-count series).

A durable time series behind the radar's "who's heating up" signal — one row per
(employer, day). Complements ats_jobs.first_seen (which gives "opened in last N
days" immediately) with a real open-count trajectory that accrues forward.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "commute_shed_stat",
        sa.Column("captured_on", sa.Date(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("open_roles", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("captured_on", "token"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("commute_shed_stat", schema=SCHEMA)
