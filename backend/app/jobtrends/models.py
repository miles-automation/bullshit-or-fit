"""RAW storage for HN monthly-thread ingestion.

Two tables in a dedicated `jobtrends` Postgres schema (namespaced away from any
future Bullshit or Fit product tables, and never coupled to Human Index):

  hn_hiring_threads  — one row per monthly story (provenance)
  hn_hiring_posts    — one row per top-level post, RAW body verbatim

Each row is tagged with a `source` ('hn') and a `stream` — the semantic thread
type. This is the multi-source spine: today the streams are the HN monthly
threads ('hiring' = jobs/demand, 'wants_hired' = candidates/supply); future
sources (ATS/remote boards) will add their own raw tables feeding the same
derived layer. (The table names keep the legacy `hn_hiring_` prefix; a rename to
`hn_*` is a cosmetic cleanup for later.)

The HN ids are the natural primary keys, which makes ingestion idempotent: a
re-run upserts on `hn_id` and never duplicates. We keep raw text forever; all
analysis reconstructs from these rows.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SCHEMA = "jobtrends"

SOURCE_HN = "hn"
# Semantic thread types. 'hiring' is the original corpus (job posts / demand);
# 'wants_hired' is the candidate/supply side.
STREAM_HIRING = "hiring"
STREAM_WANTS_HIRED = "wants_hired"


class HnHiringThread(Base):
    __tablename__ = "hn_hiring_threads"
    __table_args__ = (
        UniqueConstraint(
            "source", "stream", "month", name="uq_hn_threads_stream_month"
        ),
        {"schema": SCHEMA},
    )

    # HN story objectID — supplied by us, not generated.
    hn_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{SOURCE_HN}'")
    )
    stream: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{STREAM_HIRING}'")
    )
    # First-of-month bucket (e.g. 2026-07-01). One thread per (source, stream, month).
    month: Mapped[date] = mapped_column(Date, nullable=False)
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
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{SOURCE_HN}'")
    )
    stream: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{STREAM_HIRING}'"), index=True
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
    """Derived: per-month author cohort stats (recurrence/churn). Rebuilt from raw.

    Scoped to the hiring stream (company churn); the candidate side would be a
    separate future rollup.
    """

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


class StreamMonth(Base):
    """Derived: per-(source, stream, month) volume. Rebuilt from raw.

    The multi-stream signal: comparing 'hiring' vs 'wants_hired' post counts gives
    a demand/supply read on the market (job-seekers per opening).
    """

    __tablename__ = "stream_month"
    __table_args__ = {"schema": SCHEMA}

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    stream: Mapped[str] = mapped_column(Text, primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    post_count: Mapped[int] = mapped_column(Integer, nullable=False)
    author_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AtsJob(Base):
    """RAW: a single ATS job posting (source='ats'), one row per role.

    A continuous-board source (Greenhouse today), unlike the HN monthly archive:
    ATS APIs expose only *currently open* roles, so we snapshot on each run and
    track `first_seen`/`last_seen`/`is_open` to accrue open→closed history over
    time. `id` = '<provider>:<company_token>:<external_id>' (stable per role).
    """

    __tablename__ = "ats_jobs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'ats'")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    company_token: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    is_open: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), index=True
    )
    # Comp is optional per role. 'structured' = a real pay field (USAJobs
    # PositionRemuneration); 'parsed' = the free-text heuristic over title+content.
    # Amounts are annualized in the row's own currency.
    comp_min: Mapped[int | None] = mapped_column(Integer)
    comp_max: Mapped[int | None] = mapped_column(Integer)
    comp_currency: Mapped[str | None] = mapped_column(Text)
    comp_period: Mapped[str | None] = mapped_column(Text)
    comp_kind: Mapped[str | None] = mapped_column(Text)


class CommuteShedEmployer(Base):
    """Reference: a single employer reachable from a home base (Laramie, WY).

    The commute-shed radar's spine — a curated map of *who's around*, tiered by
    reachability (`tier`): 'laramie' (onsite in town), 'cheyenne' (~50 mi),
    'front_range' (~1–1.5 hr, NoCo/CO) and 'wy_remote' (WY-domiciled, remote-first).

    Reference data, not derived: seeded from `commute_shed.SEED_EMPLOYERS` and
    upserted each tick, so curating the code list is the source of truth. Every row
    carries a `careers_url` (the warm link where we have no machine feed) and,
    where a public board exists, a (`provider`, `ats_token`) that the snapshot pulls
    live into `ats_jobs` (source='commute_shed'). Live open-role counts are joined
    back by `ats_token`, so the map is complete day one — counts where we have a
    feed, a link + note everywhere else.
    """

    __tablename__ = "commute_shed_employer"
    __table_args__ = {"schema": SCHEMA}

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    hq_city: Mapped[str | None] = mapped_column(Text)
    hq_state: Mapped[str | None] = mapped_column(Text)
    # Rough driving miles from Laramie, WY (0 = in town). Directional, for sorting.
    distance_mi: Mapped[int | None] = mapped_column(Integer)
    # Machine feed, when one exists: provider is greenhouse/lever/ashby, ats_token
    # the board slug. Null => map-only (careers_url + notes carry it).
    provider: Mapped[str | None] = mapped_column(Text)
    ats_token: Mapped[str | None] = mapped_column(Text)
    careers_url: Mapped[str] = mapped_column(Text, nullable=False)
    engineer_relevant: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommuteShedStat(Base):
    """History: one row per (employer, day) recording that day's open-role count.

    The commute-shed radar's velocity spine. `ats_jobs` tracks each role's
    first_seen/last_seen, which yields "opened in the last N days" immediately; this
    table adds a durable per-employer open-count time series so a real trajectory
    ("open roles 30 days ago vs now", sparklines, heating/cooling) accrues from first
    deploy forward. Upserted once per snapshot tick on (captured_on, token), so
    multiple ticks in a day just refresh the day's count.
    """

    __tablename__ = "commute_shed_stat"
    __table_args__ = {"schema": SCHEMA}

    captured_on: Mapped[date] = mapped_column(Date, primary_key=True)
    token: Mapped[str] = mapped_column(Text, primary_key=True)
    open_roles: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KeywordSourceDemand(Base):
    """Derived: keyword presence across CURRENTLY-OPEN continuous-board roles,
    per source. Rebuilt from `ats_jobs` (title + content_text) via the taxonomy.

    This is the cross-source unification: the same skill taxonomy that drives HN's
    historical keyword trend, applied to the live ATS / remote / federal openings —
    a snapshot of what's in demand *right now*, comparable across sources.
    """

    __tablename__ = "keyword_source_demand"
    __table_args__ = {"schema": SCHEMA}

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    keyword: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    roles_matched: Mapped[int] = mapped_column(Integer, nullable=False)
    roles_total: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OewsWage(Base):
    """BLS OEWS wage percentiles for an occupation by area (national + per state).

    The location-real, all-employer comp signal — sourced via O*NET (bls.gov
    bot-blocks). Refreshed occasionally (OEWS is annual); reads back per area.
    """

    __tablename__ = "oews_wage"
    __table_args__ = {"schema": SCHEMA}

    soc: Mapped[str] = mapped_column(Text, primary_key=True)
    occupation: Mapped[str] = mapped_column(Text, nullable=False)
    area_type: Mapped[str] = mapped_column(Text, nullable=False)
    area_code: Mapped[str] = mapped_column(Text, primary_key=True)
    area_name: Mapped[str] = mapped_column(Text, nullable=False)
    p10_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    p25_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    median_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    p75_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    p90_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WarnNotice(Base):
    """RAW: a single WARN Act layoff filing (source='warn'), one row per notice.

    A supply-side signal — workers entering the market — from state labor
    departments' public WARN databases (Socrata JSON feeds). Immutable history:
    a past filing doesn't change, so ingestion upserts on a deterministic `id`
    ('<state>:<external-or-composite>') and never duplicates.
    """

    __tablename__ = "warn_notices"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str | None] = mapped_column(Text)
    employees_affected: Mapped[int | None] = mapped_column(Integer)
    notice_date: Mapped[date | None] = mapped_column(Date, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date)
    layoff_type: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WarnMonth(Base):
    """Derived: WARN filings + employees affected per month, from warn_notices
    (bucketed by notice/announcement date). Rebuilt each tick."""

    __tablename__ = "warn_month"
    __table_args__ = {"schema": SCHEMA}

    month: Mapped[date] = mapped_column(Date, primary_key=True)
    notices: Mapped[int] = mapped_column(Integer, nullable=False)
    employees_affected: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LocationStat(Base):
    """Derived: role counts per (source, metro bucket) with a remote tally.

    Rebuilt each tick from the currently-open `ats_jobs`: the messy free-text
    location is normalized to a metro bucket (or 'Remote'/'Other'), and each role
    is also flagged remote if its location mentions it. Powers "where the jobs
    are" + remote-share on the dashboard.
    """

    __tablename__ = "location_stat"
    __table_args__ = {"schema": SCHEMA}

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    n_roles: Mapped[int] = mapped_column(Integer, nullable=False)
    remote_roles: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillCompStat(Base):
    """Derived: median advertised pay per (source, skill), on the annualized-USD
    axis. Rebuilt each tick by joining the keyword taxonomy with the comp signal —
    over ats_jobs (comp columns) and HN post_comp — so a skill's pay is comparable
    across channels. Only cells with a minimum sample survive (noise floor).
    """

    __tablename__ = "skill_comp_stat"
    __table_args__ = {"schema": SCHEMA}

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    keyword: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    n_with_comp: Mapped[int] = mapped_column(Integer, nullable=False)
    p25_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    median_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    p75_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompSourceStat(Base):
    """Derived: pay quartiles per source, on one comparable axis (annualized USD).

    Rolls up comp from every source — HN posts (`post_comp`) plus the live
    continuous-board openings (`ats_jobs` comp columns: companies / remote /
    federal) — so median pay is finally comparable across channels. Rebuilt each
    tick; `n_roles` is the denominator that yields `coverage_pct`.
    """

    __tablename__ = "comp_source_stat"
    __table_args__ = {"schema": SCHEMA}

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    n_roles: Mapped[int] = mapped_column(Integer, nullable=False)
    n_with_comp: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_pct: Mapped[float] = mapped_column(Float, nullable=False)
    p25_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    median_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    p75_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
