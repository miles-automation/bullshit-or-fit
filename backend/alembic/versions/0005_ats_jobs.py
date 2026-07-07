"""jobtrends: ats_jobs raw table (ATS company boards).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "ats_jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'ats'"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("company_token", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_open", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_ats_jobs_company_token", "ats_jobs", ["company_token"], schema=SCHEMA)
    op.create_index("ix_ats_jobs_last_seen", "ats_jobs", ["last_seen"], schema=SCHEMA)
    op.create_index("ix_ats_jobs_is_open", "ats_jobs", ["is_open"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_ats_jobs_is_open", table_name="ats_jobs", schema=SCHEMA)
    op.drop_index("ix_ats_jobs_last_seen", table_name="ats_jobs", schema=SCHEMA)
    op.drop_index("ix_ats_jobs_company_token", table_name="ats_jobs", schema=SCHEMA)
    op.drop_table("ats_jobs", schema=SCHEMA)
