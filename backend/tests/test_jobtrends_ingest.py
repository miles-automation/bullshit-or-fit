"""Unit tests for jobtrends ingest parsing + the HN Algolia client.

These are DB-free: the pure parse functions and the HTTP client (driven by an
httpx MockTransport) cover the ingest LOGIC. The DB upsert path is exercised
separately where a Postgres instance is available.
"""

from datetime import UTC, date, datetime

import httpx
import pytest

from app.jobtrends.hn_algolia import HNAlgoliaClient
from app.jobtrends.ingest import (
    ingest_recent,
    is_hiring_story,
    parse_posts,
    parse_thread,
)

# July 2026 "Who is hiring?" story, created 2026-07-01T00:00:00Z (created_at_i).
JULY_STORY = {
    "objectID": "48747976",
    "title": "Ask HN: Who is hiring? (July 2026)",
    "author": "whoishiring",
    "created_at_i": 1_782_864_000,  # 2026-07-01T00:00:00Z
    "num_comments": 341,
}

HIRED_STORY = {
    "objectID": "48747999",
    "title": "Ask HN: Who wants to be hired? (July 2026)",
    "author": "whoishiring",
    "created_at_i": 1_782_864_050,
    "num_comments": 479,
}


# --- title guard -----------------------------------------------------------


def test_is_hiring_story_accepts_hiring_thread() -> None:
    assert is_hiring_story(JULY_STORY) is True


def test_is_hiring_story_rejects_wants_to_be_hired() -> None:
    assert is_hiring_story(HIRED_STORY) is False


def test_is_hiring_story_handles_missing_title() -> None:
    assert is_hiring_story({"objectID": "1"}) is False


# --- thread parsing --------------------------------------------------------


def test_parse_thread_buckets_to_first_of_month() -> None:
    thread = parse_thread(JULY_STORY)
    assert thread.hn_id == 48747976
    assert thread.month == date(2026, 7, 1)
    assert thread.num_comments == 341
    assert thread.posted_at == datetime(2026, 7, 1, tzinfo=UTC)


def test_parse_thread_requires_timestamp() -> None:
    with pytest.raises(ValueError):
        parse_thread({"objectID": "1", "title": "Ask HN: Who is hiring?"})


# --- post parsing ----------------------------------------------------------


def test_parse_posts_keeps_top_level_with_text_skips_empty() -> None:
    thread = parse_thread(JULY_STORY)
    item = {
        "id": 48747976,
        "children": [
            {
                "id": 1,
                "author": "acme",
                "text": "Acme | Remote | $150k",
                "created_at_i": 1_782_864_100,
            },
            {"id": 2, "author": "deleted", "text": None},  # deleted -> skipped
            {"id": 3, "author": None, "text": ""},  # empty -> skipped
            {
                "id": 4,
                "author": "globex",
                "text": "Globex | Onsite",
                "created_at_i": None,
            },
        ],
    }
    posts = parse_posts(item, thread)
    assert [p.hn_id for p in posts] == [1, 4]
    assert posts[0].raw_text == "Acme | Remote | $150k"
    assert posts[0].month == date(2026, 7, 1)
    assert posts[0].thread_id == 48747976
    assert posts[0].posted_at == datetime(2026, 7, 1, 0, 1, 40, tzinfo=UTC)
    assert posts[1].posted_at is None  # missing created_at_i tolerated


def test_parse_posts_no_children() -> None:
    thread = parse_thread(JULY_STORY)
    assert parse_posts({"id": 48747976}, thread) == []


# --- HTTP client (MockTransport) ------------------------------------------


def _client_with(handler) -> HNAlgoliaClient:
    return HNAlgoliaClient(transport=httpx.MockTransport(handler))


def test_search_hiring_stories_sends_expected_query() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        assert request.url.path.endswith("/search_by_date")
        return httpx.Response(200, json={"hits": [JULY_STORY]})

    hits = _client_with(handler).search_hiring_stories(6)
    assert hits == [JULY_STORY]
    assert seen["tags"] == "story,author_whoishiring"
    assert seen["query"] == "Ask HN: Who is hiring?"
    assert seen["hitsPerPage"] == "6"


def test_fetch_item_returns_json() -> None:
    payload = {"id": 48747976, "children": [{"id": 1, "text": "hi"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/items/48747976")
        return httpx.Response(200, json=payload)

    assert _client_with(handler).fetch_item(48747976) == payload


def test_client_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        _client_with(handler).search_hiring_stories(1)


# --- ingest_recent title filtering (no DB; fake session) -------------------


class _FakeSession:
    """Captures execute/commit without a real DB, so ingest_recent's control
    flow (title filtering, per-month iteration) is testable in isolation."""

    def __init__(self) -> None:
        self.commits = 0

    def execute(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        return None

    def commit(self) -> None:
        self.commits += 1


def test_ingest_recent_filters_out_inverse_thread() -> None:
    children = {
        "children": [
            {"id": 10, "author": "a", "text": "job", "created_at_i": 1_782_864_100}
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search_by_date"):
            # API returns both thread types interleaved; only hiring should ingest.
            return httpx.Response(200, json={"hits": [JULY_STORY, HIRED_STORY]})
        return httpx.Response(200, json={"id": 48747976, **children})

    session = _FakeSession()
    result = ingest_recent(session, _client_with(handler), months=5)

    assert list(result.keys()) == ["2026-07-01"]  # inverse thread excluded
    assert result["2026-07-01"] == 1
    assert session.commits == 1
