"""Tests for the concept-testing harness (pure logic + wiring).

The buy-click contract, clause by clause (frontend render clauses 1/2/5 are
covered in frontend/src/ConceptLanding.test.tsx):
  1. real price before the CTA          — concepts carry real tier prices
  2. only explicit intent clicks logged — event types are distinct + validated
  3. label completeness                 — price/version/channel/creative/session stored
  4. no payment path                    — round 1 has no checkout_url anywhere
  5. post-click "not available yet"     — frontend test
  6. outcome joins back to the selector — provenance is data, stamped on events
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.experiments import (
    CONCEPTS,
    EVENT_INTENT,
    EVENT_RESERVE,
    EVENT_VIEW,
    ExperimentEvent,
    content_fingerprint,
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


def test_round1_has_no_payment_path() -> None:
    """Contract clause 4: no checkout_url and no Stripe link anywhere in round 1."""
    for c in CONCEPTS:
        for t in c.tiers:
            assert t.checkout_url is None


# The version pin: everything a visitor can see, hashed. If this test fails you
# changed concept copy or pricing — bump that concept's `version` AND update its
# pin here (labels logged under the old fingerprint are a different experiment).
_VERSION_PINS: dict[str, tuple[int, str]] = {
    "document-data-extraction": (1, "e86719752433c0d8"),
    "bookkeeping-invoice-automation": (1, "3e59fe7883015082"),
    "lead-generation-research": (1, "f9f0cc619beabba8"),
}


def test_version_is_enforced_by_content_fingerprint() -> None:
    assert set(_VERSION_PINS) == {c.slug for c in CONCEPTS}, (
        "concept added/removed — update _VERSION_PINS"
    )
    for c in CONCEPTS:
        version, fp = _VERSION_PINS[c.slug]
        assert (c.version, content_fingerprint(c)) == (version, fp), (
            f"{c.slug}: visible content changed — bump `version` and re-pin "
            f"(new fingerprint: {content_fingerprint(c)})"
        )


# A slug's provenance is IMMUTABLE. The outcome log joins on candidate_ref, and
# log_event stamps it from CURRENT config — so reassigning a slug to a different
# selector run would rebind in-flight pages' labels to the wrong candidate. If
# this pin fails you tried exactly that: give the new candidate a NEW slug and
# retire the old concept (active=False) instead.
_PROVENANCE_PINS: dict[str, str] = {
    "document-data-extraction": (
        "spark-swarm/venture-ideator-founder-fit:shot-ranker/round-1"
        "#document-data-extraction"
    ),
    "bookkeeping-invoice-automation": (
        "spark-swarm/venture-ideator-founder-fit:shot-ranker/round-1"
        "#bookkeeping-invoice-automation"
    ),
    "lead-generation-research": (
        "spark-swarm/venture-ideator-founder-fit:shot-ranker/round-1"
        "#lead-generation-research"
    ),
}


def test_provenance_is_immutable_per_slug() -> None:
    assert {c.slug: c.provenance.ref for c in CONCEPTS} == _PROVENANCE_PINS, (
        "a slug's provenance changed — a different selector run must be a NEW "
        "slug (retire the old concept), or in-flight labels join the wrong "
        "candidate"
    )


def test_provenance_joins_back_to_the_selector() -> None:
    """Contract clause 6: every concept carries a real, stored candidate reference
    (not a comment) joining the outcome to the candidate + experiment."""
    for c in CONCEPTS:
        p = c.provenance
        assert p.experiment and p.candidate and p.features and p.sources
        # the concept slug IS the join key on the spark-swarm side (slugify(shot.domain))
        assert p.candidate == c.slug
        assert p.experiment in p.ref and p.candidate in p.ref
        assert c.version >= 1


# ---- log_event guards (no DB needed on the reject path) -------------------


class _Boom:
    def add(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("must not write on a rejected event")

    def commit(self):  # noqa: ANN201
        raise AssertionError("must not commit on a rejected event")


def test_log_event_rejects_bad_type_and_unknown_slug() -> None:
    b = _Boom()
    assert log_event(b, concept_slug=CONCEPTS[0].slug, event_type="bogus") is False  # type: ignore[arg-type]
    assert log_event(b, concept_slug="does-not-exist", event_type=EVENT_VIEW) is False  # type: ignore[arg-type]


# ---- label stamping (clause 3 + 6: the click must be interpretable later) --


class _Capture:
    def __init__(self) -> None:
        self.added: list[ExperimentEvent] = []

    def add(self, obj: ExperimentEvent) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass


def test_intent_is_stamped_with_price_version_and_candidate_ref() -> None:
    """The intent label carries the echoed impression (price + version as
    rendered), the server-stamped candidate ref, and the full channel/creative
    context."""
    c = CONCEPTS[0]
    cap = _Capture()
    assert log_event(
        cap,  # type: ignore[arg-type]
        concept_slug=c.slug,
        event_type=EVENT_INTENT,
        tier=c.tiers[0].name,
        price_shown=c.tiers[0].price,
        concept_version=c.version,
        session_id="sid-1",
        utm_source="reddit",
        utm_campaign="round1",
        utm_content="creative-a",
    )
    (ev,) = cap.added
    assert ev.price_shown == c.tiers[0].price
    assert ev.concept_version == c.version
    assert ev.candidate_ref == c.provenance.ref
    assert ev.utm_content == "creative-a"
    assert ev.session_id == "sid-1"


def test_intent_requires_a_tier_and_the_full_impression_echo() -> None:
    """An intent whose price cannot be attested is not a purchase-intent label:
    reject it rather than store (or relabel from current config) an unpriceable
    row in the money metric."""
    c = CONCEPTS[0]
    good = {
        "tier": c.tiers[0].name,
        "price_shown": c.tiers[0].price,
        "concept_version": c.version,
    }
    b = _Boom()
    for missing in ("tier", "price_shown", "concept_version"):
        args = {**good, missing: None}
        assert (
            log_event(b, concept_slug=c.slug, event_type=EVENT_INTENT, **args) is False  # type: ignore[arg-type]
        )
    assert (
        log_event(
            b,  # type: ignore[arg-type]
            concept_slug=c.slug,
            event_type=EVENT_INTENT,
            **{**good, "tier": "No Such Tier"},
        )
        is False
    )
    # a malformed version echo (versions start at 1) is rejected for ANY event —
    # the falsy 0 must not slip through to be "filled" with the current version
    assert (
        log_event(
            b,  # type: ignore[arg-type]
            concept_slug=c.slug,
            event_type=EVENT_INTENT,
            **{**good, "concept_version": 0},
        )
        is False
    )
    assert (
        log_event(b, concept_slug=c.slug, event_type=EVENT_VIEW, concept_version=0)  # type: ignore[arg-type]
        is False
    )
    # views and reserves are fine without a tier or echo
    cap = _Capture()
    assert log_event(cap, concept_slug=c.slug, event_type=EVENT_VIEW)  # type: ignore[arg-type]
    assert log_event(cap, concept_slug=c.slug, event_type=EVENT_RESERVE)  # type: ignore[arg-type]


def test_reserve_fallback_never_pairs_old_version_with_new_price() -> None:
    """A reserve that echoes a STALE version but no price must store price NULL
    (honestly unknown), not current config's price — and a current-version
    reserve gets the config fill."""
    c = CONCEPTS[0]
    cap = _Capture()
    assert log_event(
        cap,  # type: ignore[arg-type]
        concept_slug=c.slug,
        event_type=EVENT_RESERVE,
        tier=c.tiers[0].name,
        concept_version=c.version + 1,  # stale/mismatched page
    )
    assert log_event(
        cap,  # type: ignore[arg-type]
        concept_slug=c.slug,
        event_type=EVENT_RESERVE,
        tier=c.tiers[0].name,
        concept_version=c.version,  # current page, echo omitted
    )
    stale, current = cap.added
    assert stale.price_shown is None
    assert current.price_shown == c.tiers[0].price


def test_impression_echo_wins_over_current_config() -> None:
    """The client echoes the impression it rendered. If a deploy repriced the
    concept between page load and click, the echo — not current config — is the
    observation, so it is what gets stored."""
    c = CONCEPTS[0]
    cap = _Capture()
    assert log_event(
        cap,  # type: ignore[arg-type]
        concept_slug=c.slug,
        event_type=EVENT_INTENT,
        tier=c.tiers[0].name,
        price_shown="$19/mo",  # what an older deploy showed
        concept_version=c.version + 1,
    )
    (ev,) = cap.added
    assert ev.price_shown == "$19/mo"
    assert ev.concept_version == c.version + 1


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
    ver = CONCEPTS[0].version
    rows = [
        (first, EVENT_VIEW, ver, 100),
        (first, EVENT_INTENT, ver, 3),  # 3% intent
        (second, EVENT_VIEW, ver, 100),
        (second, EVENT_INTENT, ver, 8),  # 8% intent → should rank first
        # a stale version's rows must NOT blend into the current funnel
        (first, EVENT_INTENT, ver + 1, 500),
    ]
    funnels = experiment_summary(_FakeSession(rows))  # type: ignore[arg-type]
    assert funnels[0].slug == second and funnels[0].intent_rate == 8.0
    f_first = next(f for f in funnels if f.slug == first)
    assert f_first.views == 100 and f_first.intents == 3 and f_first.intent_rate == 3.0


# ---- session dedup, against a real database (clause 3: DEDUPLICATED session) --


def _real_session() -> Session:
    """In-memory SQLite with an attached `experiments` schema so the real summary
    SQL (DISTINCT + FILTER) runs against real rows."""
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS experiments")
    ExperimentEvent.__table__.create(engine)
    return Session(engine)


def test_summary_deduplicates_sessions() -> None:
    """One person clicking twice is ONE intent. Session-less events can't be
    deduplicated, so each counts once."""
    slug = CONCEPTS[0].slug
    with _real_session() as s:
        for sid in ("v1", "v1", "v2"):  # 2 unique viewers
            log_event(s, concept_slug=slug, event_type=EVENT_VIEW, session_id=sid)
        t = CONCEPTS[0].tiers[0]
        for sid in ("a", "a", "b", None):  # a double-clicked; one event lost its sid
            log_event(
                s,
                concept_slug=slug,
                event_type=EVENT_INTENT,
                tier=t.name,
                price_shown=t.price,
                concept_version=CONCEPTS[0].version,
                session_id=sid,
            )
        log_event(s, concept_slug=slug, event_type=EVENT_RESERVE, session_id="a")
        f = next(x for x in experiment_summary(s) if x.slug == slug)
    assert f.views == 2
    assert f.intents == 3  # {a, b} + one session-less event
    assert f.reserves == 1
    assert f.intent_rate == 150.0  # 3 intents / 2 views — honest, if odd-looking


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
    r = client.post(
        f"/api/v1/exp/{slug}/event",
        json={
            "event_type": EVENT_VIEW,
            "session_id": "sid",
            "utm_source": "reddit",
            "utm_campaign": "round1",
            "utm_content": "creative-a",
        },
    )
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
