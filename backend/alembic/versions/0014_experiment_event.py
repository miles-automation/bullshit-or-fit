"""experiments: fake-door concept-testing event log.

Append-only funnel events (view / intent / reserve) from the concept landing
pages, used to measure payment-intent per concept and pick what to build.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"

SCHEMA = "experiments"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "experiment_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("concept_slug", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),  # view | intent | reserve
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("utm_source", sa.Text(), nullable=True),
        sa.Column("utm_campaign", sa.Text(), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_experiment_event_concept_slug",
        "experiment_event",
        ["concept_slug"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_experiment_event_created_at",
        "experiment_event",
        ["created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("experiment_event", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
