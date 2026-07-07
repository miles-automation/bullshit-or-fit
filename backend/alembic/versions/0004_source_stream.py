"""jobtrends: source/stream columns + stream_month (multi-stream spine).

Adds `source`/`stream` to the raw HN tables (existing rows default to hn/hiring),
swaps the threads unique(month) for unique(source, stream, month) so multiple
streams can share a month, and adds the derived `stream_month` volume table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-07
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"

SCHEMA = "jobtrends"


def upgrade() -> None:
    for table in ("hn_hiring_threads", "hn_hiring_posts"):
        op.add_column(
            table,
            sa.Column("source", sa.Text(), nullable=False, server_default="hn"),
            schema=SCHEMA,
        )
        op.add_column(
            table,
            sa.Column("stream", sa.Text(), nullable=False, server_default="hiring"),
            schema=SCHEMA,
        )
    op.create_index("ix_hn_hiring_posts_stream", "hn_hiring_posts", ["stream"], schema=SCHEMA)

    # One thread per month became one thread per (source, stream, month).
    op.drop_constraint("uq_hn_hiring_threads_month", "hn_hiring_threads", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_hn_threads_stream_month",
        "hn_hiring_threads",
        ["source", "stream", "month"],
        schema=SCHEMA,
    )

    op.create_table(
        "stream_month",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("stream", sa.Text(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=False),
        sa.Column("author_count", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "stream", "month"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("stream_month", schema=SCHEMA)
    op.drop_constraint(
        "uq_hn_threads_stream_month", "hn_hiring_threads", schema=SCHEMA, type_="unique"
    )
    op.create_unique_constraint(
        "uq_hn_hiring_threads_month", "hn_hiring_threads", ["month"], schema=SCHEMA
    )
    op.drop_index("ix_hn_hiring_posts_stream", table_name="hn_hiring_posts", schema=SCHEMA)
    for table in ("hn_hiring_posts", "hn_hiring_threads"):
        op.drop_column(table, "stream", schema=SCHEMA)
        op.drop_column(table, "source", schema=SCHEMA)
