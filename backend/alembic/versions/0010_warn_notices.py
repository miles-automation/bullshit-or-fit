"""jobtrends: WARN Act layoff filings (supply-side source).

Raw `warn_notices` (immutable filing events) + derived `warn_month` (employees
affected per month). A supply signal — workers entering the market — distinct from
the demand-side job postings, so it gets its own raw table rather than ats_jobs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "warn_notices",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("employees_affected", sa.Integer(), nullable=True),
        sa.Column("notice_date", sa.Date(), nullable=True, index=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("layoff_type", sa.Text(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "warn_month",
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("notices", sa.Integer(), nullable=False),
        sa.Column("employees_affected", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("warn_month", schema=SCHEMA)
    op.drop_table("warn_notices", schema=SCHEMA)
