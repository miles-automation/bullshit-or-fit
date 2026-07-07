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
    STREAMS,
    ingest_recent,
    matches_stream,
    parse_posts,
    parse_thread,
)

HIRING = STREAMS[0]  # 'hiring'
WANTS = STREAMS[1]  # 'wants_hired'

# July 2026 stories, created 2026-07-01T00:00:00Z (created_at_i).
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


# --- stream title guards ---------------------------------------------------


def test_matches_stream_hiring() -> None:
    assert matches_stream(JULY_STORY, HIRING) is True
    assert matches_stream(HIRED_STORY, HIRING) is False


def test_matches_stream_wants_hired() -> None:
    assert matches_stream(HIRED_STORY, WANTS) is True
    assert matches_stream(JULY_STORY, WANTS) is False


def test_matches_stream_handles_missing_title() -> None:
    assert matches_stream({"objectID": "1"}, HIRING) is False


# --- thread parsing --------------------------------------------------------


def test_parse_thread_buckets_and_tags_stream() -> None:
    thread = parse_thread(JULY_STORY, "hiring")
    assert thread.hn_id == 48747976
    assert thread.source == "hn"
    assert thread.stream == "hiring"
    assert thread.month == date(2026, 7, 1)
    assert thread.num_comments == 341
    assert thread.posted_at == datetime(2026, 7, 1, tzinfo=UTC)


def test_parse_thread_requires_timestamp() -> None:
    with pytest.raises(ValueError):
        parse_thread({"objectID": "1", "title": "Ask HN: Who is hiring?"}, "hiring")


# --- post parsing ----------------------------------------------------------


def test_parse_posts_keeps_top_level_with_text_skips_empty() -> None:
    thread = parse_thread(HIRED_STORY, "wants_hired")
    item = {
        "id": 48747999,
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
    assert posts[0].stream == "wants_hired"  # posts inherit the thread's stream
    assert posts[0].source == "hn"
    assert posts[0].thread_id == 48747999
    assert posts[0].posted_at == datetime(2026, 7, 1, 0, 1, 40, tzinfo=UTC)
    assert posts[1].posted_at is None  # missing created_at_i tolerated


def test_parse_posts_no_children() -> None:
    thread = parse_thread(JULY_STORY, "hiring")
    assert parse_posts({"id": 48747976}, thread) == []


# --- HTTP client (MockTransport) ------------------------------------------


def _client_with(handler) -> HNAlgoliaClient:
    return HNAlgoliaClient(transport=httpx.MockTransport(handler))


def test_search_stream_stories_sends_query() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        assert request.url.path.endswith("/search_by_date")
        return httpx.Response(200, json={"hits": [JULY_STORY]})

    hits = _client_with(handler).search_stream_stories("Ask HN: Who is hiring?", 6)
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
        _client_with(handler).search_stream_stories("q", 1)


# --- ingest_recent across streams (no DB; fake session) --------------------


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def execute(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        return None

    def commit(self) -> None:
        self.commits += 1


def test_ingest_recent_ingests_each_stream_with_title_guard() -> None:
    children = {
        "children": [
            {"id": 10, "author": "a", "text": "post", "created_at_i": 1_782_864_100}
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search_by_date"):
            # Both thread types come back interleaved; the per-stream title guard
            # must pick the right one for each query.
            return httpx.Response(200, json={"hits": [JULY_STORY, HIRED_STORY]})
        return httpx.Response(200, json={"id": 1, **children})

    session = _FakeSession()
    result = ingest_recent(session, _client_with(handler), months=5)

    assert set(result.keys()) == {"hiring/2026-07-01", "wants_hired/2026-07-01"}
    assert result["hiring/2026-07-01"] == 1
    assert result["wants_hired/2026-07-01"] == 1
    assert session.commits == 2  # one per stream-month
