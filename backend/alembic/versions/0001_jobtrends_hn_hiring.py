"""jobtrends: HN 'Who is hiring?' raw storage.

Creates the `jobtrends` schema and its two raw tables (threads + posts).

Revision ID: 0001
Revises:
Create Date: 2026-07-06
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None

SCHEMA = "jobtrends"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "hn_hiring_threads",
        sa.Column("hn_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("num_comments", sa.Integer(), nullable=True),
        sa.Column("post_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("hn_id"),
        sa.UniqueConstraint("month", name="uq_hn_hiring_threads_month"),
        schema=SCHEMA,
    )

    op.create_table(
        "hn_hiring_posts",
        sa.Column("hn_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.hn_hiring_threads.hn_id"],
        ),
        sa.PrimaryKeyConstraint("hn_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hn_hiring_posts_thread_id", "hn_hiring_posts", ["thread_id"], schema=SCHEMA
    )
    op.create_index("ix_hn_hiring_posts_month", "hn_hiring_posts", ["month"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_hn_hiring_posts_month", table_name="hn_hiring_posts", schema=SCHEMA)
    op.drop_index("ix_hn_hiring_posts_thread_id", table_name="hn_hiring_posts", schema=SCHEMA)
    op.drop_table("hn_hiring_posts", schema=SCHEMA)
    op.drop_table("hn_hiring_threads", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
