"""HN "Who is hiring?" ingestion: thread -> posts, month bucketing, idempotent upsert.

Parsing (story hit -> ParsedThread, item -> ParsedPost[]) is pure and free of any
DB or network concern, so it is unit-testable in isolation. The write path
upserts on the HN ids, so re-running any month refreshes edited bodies but never
duplicates rows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.models import HnHiringPost, HnHiringThread

logger = logging.getLogger(__name__)

# `whoishiring` also posts "Who wants to be hired?" (the inverse thread) each
# month; the free-text query filters them out, and this is the belt-and-suspenders
# guard so a relevance-ranking wobble can never let one through.
HIRING_TITLE_RE = re.compile(r"^\s*Ask HN:\s*Who is hiring\?", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedThread:
    hn_id: int
    month: date
    title: str
    posted_at: datetime | None
    num_comments: int | None


@dataclass(frozen=True)
class ParsedPost:
    hn_id: int
    thread_id: int
    month: date
    author: str | None
    posted_at: datetime | None
    raw_text: str


def _ts_to_dt(created_at_i: Any) -> datetime | None:
    if created_at_i is None:
        return None
    return datetime.fromtimestamp(int(created_at_i), tz=UTC)


def is_hiring_story(hit: dict[str, Any]) -> bool:
    return bool(HIRING_TITLE_RE.match(str(hit.get("title") or "")))


def parse_thread(hit: dict[str, Any]) -> ParsedThread:
    posted_at = _ts_to_dt(hit.get("created_at_i"))
    if posted_at is None:
        raise ValueError(f"story {hit.get('objectID')} has no created_at_i")
    return ParsedThread(
        hn_id=int(hit["objectID"]),
        month=posted_at.date().replace(day=1),
        title=str(hit.get("title") or ""),
        posted_at=posted_at,
        num_comments=hit.get("num_comments"),
    )


def parse_posts(item: dict[str, Any], thread: ParsedThread) -> list[ParsedPost]:
    """Top-level children with text = one job post each. Deleted/empty comments
    (no `text`) are skipped; nested replies are ignored (they are not job posts)."""
    posts: list[ParsedPost] = []
    for child in item.get("children") or []:
        text_body = child.get("text")
        if not text_body:
            continue
        posts.append(
            ParsedPost(
                hn_id=int(child["id"]),
                thread_id=thread.hn_id,
                month=thread.month,
                author=child.get("author"),
                posted_at=_ts_to_dt(child.get("created_at_i")),
                raw_text=text_body,
            )
        )
    return posts


def _upsert_thread(session: Session, thread: ParsedThread, post_count: int) -> None:
    stmt = pg_insert(HnHiringThread).values(
        hn_id=thread.hn_id,
        month=thread.month,
        title=thread.title,
        posted_at=thread.posted_at,
        num_comments=thread.num_comments,
        post_count=post_count,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[HnHiringThread.hn_id],
        set_={
            "month": stmt.excluded.month,
            "title": stmt.excluded.title,
            "posted_at": stmt.excluded.posted_at,
            "num_comments": stmt.excluded.num_comments,
            "post_count": stmt.excluded.post_count,
            "fetched_at": datetime.now(tz=UTC),
        },
    )
    session.execute(stmt)


def _upsert_posts(session: Session, posts: list[ParsedPost]) -> None:
    if not posts:
        return
    rows = [
        {
            "hn_id": p.hn_id,
            "thread_id": p.thread_id,
            "month": p.month,
            "author": p.author,
            "posted_at": p.posted_at,
            "raw_text": p.raw_text,
        }
        for p in posts
    ]
    stmt = pg_insert(HnHiringPost).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[HnHiringPost.hn_id],
        set_={
            "thread_id": stmt.excluded.thread_id,
            "month": stmt.excluded.month,
            "author": stmt.excluded.author,
            "posted_at": stmt.excluded.posted_at,
            "raw_text": stmt.excluded.raw_text,
            "fetched_at": datetime.now(tz=UTC),
        },
    )
    session.execute(stmt)


def ingest_story(session: Session, client: HNAlgoliaClient, hit: dict[str, Any]) -> int:
    """Ingest one monthly thread. Returns the number of job posts stored.

    Commits its own transaction so a long backfill makes progress month by month.
    """
    thread = parse_thread(hit)
    item = client.fetch_item(thread.hn_id)
    posts = parse_posts(item, thread)
    _upsert_thread(session, thread, len(posts))
    _upsert_posts(session, posts)
    session.commit()
    logger.info(
        "jobtrends: ingested %s (%s posts, thread %s)",
        thread.month.isoformat(),
        len(posts),
        thread.hn_id,
    )
    return len(posts)


def ingest_recent(
    session: Session, client: HNAlgoliaClient, months: int
) -> dict[str, int]:
    """Ingest the most recent `months` 'Who is hiring?' threads.

    Idempotent: safe to re-run any time. Returns {month -> posts stored}.
    """
    # Over-fetch a little so title-filtering out the "wants to be hired?" siblings
    # still leaves `months` real hiring threads.
    hits = client.search_hiring_stories(months + 2)
    hiring = [h for h in hits if is_hiring_story(h)][:months]
    result: dict[str, int] = {}
    for hit in hiring:
        thread = parse_thread(hit)
        result[thread.month.isoformat()] = ingest_story(session, client, hit)
    return result
