"""jobtrends: per-source freshness state (the staleness-alert edge trigger).

Snapshot sources fail silently — every connector logs a warning and skips
`close_missing` on a fetch error, so a total outage looks exactly like a healthy
run. USAJobs was dead 16 days (2026-07-17 → 2026-08-02) before anyone noticed.
This table records the last NOTIFIED state per source so the worker alerts once
on ok→stale rather than every tick, and so the state survives container
recreation.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "source_health",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("source_health", schema=SCHEMA)
