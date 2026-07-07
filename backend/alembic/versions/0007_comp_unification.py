"""jobtrends: cross-source comp unification.

Adds structured/parsed comp columns to `ats_jobs` (USAJobs ships structured
PositionRemuneration; Greenhouse/remote roles get free-text-parsed comp) and a
derived `comp_source_stat` table that rolls HN + companies + remote + federal pay
onto one comparable axis.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"

SCHEMA = "jobtrends"


def upgrade() -> None:
    # Per-role comp on the shared continuous-board table. All nullable — most
    # boards don't disclose pay, and 'kind' records provenance ('structured' from
    # a real pay field, 'parsed' from the free-text heuristic).
    for col in (
        sa.Column("comp_min", sa.Integer(), nullable=True),
        sa.Column("comp_max", sa.Integer(), nullable=True),
        sa.Column("comp_currency", sa.Text(), nullable=True),
        sa.Column("comp_period", sa.Text(), nullable=True),
        sa.Column("comp_kind", sa.Text(), nullable=True),
    ):
        op.add_column("ats_jobs", col, schema=SCHEMA)

    op.create_table(
        "comp_source_stat",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("n_roles", sa.Integer(), nullable=False),
        sa.Column("n_with_comp", sa.Integer(), nullable=False),
        sa.Column("coverage_pct", sa.Float(), nullable=False),
        sa.Column("p25_usd", sa.Integer(), nullable=False),
        sa.Column("median_usd", sa.Integer(), nullable=False),
        sa.Column("p75_usd", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("comp_source_stat", schema=SCHEMA)
    for name in ("comp_kind", "comp_period", "comp_currency", "comp_max", "comp_min"):
        op.drop_column("ats_jobs", name, schema=SCHEMA)
