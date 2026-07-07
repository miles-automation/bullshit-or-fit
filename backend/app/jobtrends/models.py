"""RAW storage for HN "Who is hiring?" ingestion.

Two tables in a dedicated `jobtrends` Postgres schema (namespaced away from any
future Bullshit or Fit product tables, and never coupled to Human Index):

  hn_hiring_threads  — one row per monthly story (provenance)
  hn_hiring_posts    — one row per top-level job post, RAW body verbatim

The HN ids are the natural primary keys, which makes ingestion idempotent: a
re-run upserts on `hn_id` and never duplicates. We keep raw text forever; all
analysis reconstructs from these rows.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SCHEMA = "jobtrends"


class HnHiringThread(Base):
    __tablename__ = "hn_hiring_threads"
    __table_args__ = {"schema": SCHEMA}

    # HN story objectID — supplied by us, not generated.
    hn_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    # First-of-month bucket (e.g. 2026-07-01). One thread per month.
    month: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Total comments HN reports (includes nested replies); posts stored is post_count.
    num_comments: Mapped[int | None] = mapped_column(Integer)
    post_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HnHiringPost(Base):
    __tablename__ = "hn_hiring_posts"
    __table_args__ = {"schema": SCHEMA}

    # HN comment id — the idempotency key.
    hn_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA}.hn_hiring_threads.hn_id"),
        nullable=False,
        index=True,
    )
    # Denormalized month bucket for fast time-series queries without a join.
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    author: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Full post body exactly as the API returns it (HTML entities and tags intact).
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KeywordMonthStat(Base):
    """Derived: per-month keyword presence, fully rebuildable from the raw posts.

    One row per (month, keyword). `posts_matched` = posts that month whose body
    matched the keyword (presence, counted once per post); `posts_total` = all posts
    that month (denormalized so trend queries need no join). The extract
    stage rebuilds this table wholesale, so it always reflects the CURRENT taxonomy
    over the CURRENT raw corpus — never a source of truth, always reconstructable.
    """

    __tablename__ = "keyword_month_stats"
    __table_args__ = {"schema": SCHEMA}

    month: Mapped[date] = mapped_column(Date, primary_key=True)
    keyword: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    posts_matched: Mapped[int] = mapped_column(Integer, nullable=False)
    posts_total: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PostComp(Base):
    """Derived: parsed salary for posts that state one. Rebuilt from raw.

    One row per post with a parseable comp figure (precision-first — noise like
    funding/stars is rejected). Amounts are annualized currency units; hourly rows
    are ×2080 with period='hour'.
    """

    __tablename__ = "post_comp"
    __table_args__ = {"schema": SCHEMA}

    hn_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA}.hn_hiring_posts.hn_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    month: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    min_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    max_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    midpoint: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    raw_match: Mapped[str] = mapped_column(Text, nullable=False)


class CohortMonth(Base):
    """Derived: per-month author cohort stats (recurrence/churn). Rebuilt from raw."""

    __tablename__ = "cohort_month"
    __table_args__ = {"schema": SCHEMA}

    month: Mapped[date] = mapped_column(Date, primary_key=True)
    active_authors: Mapped[int] = mapped_column(Integer, nullable=False)
    new_authors: Mapped[int] = mapped_column(Integer, nullable=False)
    returning_authors: Mapped[int] = mapped_column(Integer, nullable=False)
    churned_prev: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
