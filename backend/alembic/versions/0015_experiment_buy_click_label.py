"""experiments: buy-click label integrity columns.

The intent event is a training label; these columns freeze what was true at click
time so later config edits can't corrupt it: `price_shown` (tiers are code config —
editing "$29/mo" to "$49/mo" must not silently re-price historical intents),
`concept_version` (which copy/pricing revision was on screen), `candidate_ref`
(joins the outcome back to the candidate + experiment that selected the concept),
and `utm_content` (the ad creative). All nullable: pre-existing rows keep NULLs.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"

SCHEMA = "experiments"
TABLE = "experiment_event"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("price_shown", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        TABLE, sa.Column("concept_version", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(TABLE, sa.Column("candidate_ref", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("utm_content", sa.Text(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column(TABLE, "utm_content", schema=SCHEMA)
    op.drop_column(TABLE, "candidate_ref", schema=SCHEMA)
    op.drop_column(TABLE, "concept_version", schema=SCHEMA)
    op.drop_column(TABLE, "price_shown", schema=SCHEMA)
