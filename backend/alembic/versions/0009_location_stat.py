"""jobtrends: derived location_stat (geography — where the jobs are).

Normalizes the messy free-text `ats_jobs.location` into metro buckets + a remote
flag, per source, so the dashboard can show top hiring metros and remote share.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "location_stat",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("n_roles", sa.Integer(), nullable=False),
        sa.Column("remote_roles", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "bucket"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("location_stat", schema=SCHEMA)
