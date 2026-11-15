import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.create_table(
        "discovered_boards",
        sa.Column("company_slug", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("team_size", sa.Integer(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("origin", "company_slug"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_discovered_boards_found",
        "discovered_boards",
        ["found"],
        schema=SCHEMA,
    )
    op.add_column(
        "ats_jobs",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("ats_jobs", "expires_at", schema=SCHEMA)
    op.drop_index(
        "ix_discovered_boards_found", table_name="discovered_boards", schema=SCHEMA
    )
    op.drop_table("discovered_boards", schema=SCHEMA)
