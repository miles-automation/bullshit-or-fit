"""Tests for the concept-testing harness (pure logic + wiring)."""

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.experiments import (
    CONCEPTS,
    EVENT_INTENT,
    EVENT_VIEW,
    experiment_summary,
    get_concept,
    log_event,
)
from app.main import app


# ---- concept config -------------------------------------------------------


def test_concepts_are_wellformed() -> None:
    slugs = {c.slug for c in CONCEPTS}
    assert len(slugs) == len(CONCEPTS)  # unique slugs
    for c in CONCEPTS:
        assert c.headline and c.subhead and c.tiers
        assert all(t.price and t.name for t in c.tiers)


# ---- log_event guards (no DB needed on the reject path) -------------------


class _Boom:
    def add(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("must not write on a rejected event")

    def commit(self):  # noqa: ANN201
        raise AssertionError("must not commit on a rejected event")


def test_log_event_rejects_bad_type_and_unknown_slug() -> None:
    b = _Boom()
    assert log_event(b, concept_slug="tidyreceipts", event_type="bogus") is False  # type: ignore[arg-type]
    assert log_event(b, concept_slug="does-not-exist", event_type=EVENT_VIEW) is False  # type: ignore[arg-type]


# ---- summary over a fake session ------------------------------------------


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def execute(self, *_a, **_k) -> _Result:  # noqa: ANN002, ANN003
        return _Result(self._rows)


def test_summary_computes_intent_rate_and_ranks() -> None:
    first = CONCEPTS[0].slug
    second = CONCEPTS[1].slug
    rows = [
        (first, EVENT_VIEW, 100),
        (first, EVENT_INTENT, 3),  # 3% intent
        (second, EVENT_VIEW, 100),
        (second, EVENT_INTENT, 8),  # 8% intent → should rank first
    ]
    funnels = experiment_summary(_FakeSession(rows))  # type: ignore[arg-type]
    assert funnels[0].slug == second and funnels[0].intent_rate == 8.0
    f_first = next(f for f in funnels if f.slug == first)
    assert f_first.views == 100 and f_first.intents == 3 and f_first.intent_rate == 3.0


# ---- routes (DB-free empty session) ---------------------------------------


class _EmptyResult:
    def all(self) -> list:
        return []


class _EmptySession:
    def execute(self, *_a, **_k) -> _EmptyResult:  # noqa: ANN002, ANN003
        return _EmptyResult()

    def add(self, *_a, **_k) -> None:  # noqa: ANN002, ANN003
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _override_db():
    yield _EmptySession()


client = TestClient(app)


@pytest.fixture(autouse=True)
def _use_empty_db():
    # Scope the get_db override to THIS module's fake session and restore the prior
    # value after — the override is a shared global, so other test modules that set
    # it must not leak into (or be clobbered by) these tests.
    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_db
    yield
    if prev is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = prev


def test_concepts_route_lists_drafts() -> None:
    r = client.get("/api/v1/exp/concepts")
    assert r.status_code == 200
    slugs = {c["slug"] for c in r.json()}
    assert {c.slug for c in CONCEPTS} <= slugs


def test_concept_detail_and_404() -> None:
    slug = CONCEPTS[0].slug
    assert client.get(f"/api/v1/exp/concepts/{slug}").status_code == 200
    assert client.get(f"/api/v1/exp/concepts/{slug}").json()["headline"]
    assert client.get("/api/v1/exp/concepts/nope").status_code == 404


def test_event_route_ok_and_never_500s() -> None:
    slug = CONCEPTS[0].slug
    r = client.post(f"/api/v1/exp/{slug}/event", json={"event_type": EVENT_VIEW})
    assert r.status_code == 200 and r.json()["ok"] is True
    # bad type → ok:false, still 200
    r2 = client.post(f"/api/v1/exp/{slug}/event", json={"event_type": "bogus"})
    assert r2.status_code == 200 and r2.json()["ok"] is False


def test_summary_route_empty_ok() -> None:
    r = client.get("/api/v1/exp/summary")
    assert r.status_code == 200
    body = r.json()
    assert len(body["concepts"]) == len(CONCEPTS)
    assert all(c["views"] == 0 for c in body["concepts"])


def test_verify_concept() -> None:
    assert get_concept(CONCEPTS[0].slug) is not None
    assert get_concept("nope") is None
