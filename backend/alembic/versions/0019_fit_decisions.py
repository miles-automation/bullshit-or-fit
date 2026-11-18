import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "fit_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("url_key", sa.Text(), nullable=True),
        sa.Column("company_key", sa.Text(), nullable=False),
        sa.Column("title_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_fit_decisions_url_key",
        "fit_decisions",
        ["url_key"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fit_decisions_url_key", table_name="fit_decisions", schema=SCHEMA
    )
    op.drop_table("fit_decisions", schema=SCHEMA)
