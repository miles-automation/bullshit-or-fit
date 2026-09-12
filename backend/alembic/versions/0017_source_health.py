import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"

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
