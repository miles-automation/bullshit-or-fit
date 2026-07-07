"""jobtrends: derived skill_comp_stat (comp × skill cross-tab).

Median advertised pay per (source, skill) — joins the keyword taxonomy with the
comp signal so pay is comparable per skill across channels (companies vs federal
vs remote vs HN).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "skill_comp_stat",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("n_with_comp", sa.Integer(), nullable=False),
        sa.Column("p25_usd", sa.Integer(), nullable=False),
        sa.Column("median_usd", sa.Integer(), nullable=False),
        sa.Column("p75_usd", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "keyword"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("skill_comp_stat", schema=SCHEMA)
