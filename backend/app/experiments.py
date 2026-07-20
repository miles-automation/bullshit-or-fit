"""Concept-testing harness — fake-door landing pages to buy *purchase-intent* data.

The point (per the strategy): stop guessing what to build. Stand up N fake-door
landing pages (one per product concept), point paid ads at each on the same
audience/budget, and measure the ONE metric that predicts a business —
**payment intent**: someone clicking through a real price to a checkout step. CTR
and email signups measure curiosity and lie; a click on a "$29/mo — Start" button
that lands on Stripe is ~100× more predictive.

- Concepts are code config (`CONCEPTS`) rendered at `/c/{slug}`; edit + redeploy to
  add/change a concept. Each tier carries an optional `checkout_url` (a Stripe
  Payment Link) — clicking it logs `intent` and sends the visitor to real checkout.
  A concept with no link falls back to the email "reserve" flow (weaker signal).
- Events (`view`, `intent`, `reserve`) land in `experiments.experiment_event`,
  tagged with the UTM params so each ad/channel/creative/concept is separable.
  Logging is best-effort: a down DB must not break the landing page.
- The label must be interpretable later, so each event stores what the visitor
  actually SAW: the page echoes back the price + concept version it rendered
  (server config at log time is only the fallback — tiers are code config, and a
  redeploy between page load and click must not re-price the observation), plus
  the provenance ref joining the outcome back to the candidate + experiment that
  selected the concept (see `Provenance`). `version` bumps are enforced by a
  content-fingerprint pin in the tests.
- `/exp` reads `experiment_summary` → per-concept funnel + intent-rate, counted in
  UNIQUE SESSIONS (one person clicking twice is one intent) and scoped to the
  concept's CURRENT version (a bump = a new experiment). Cost-per-intent =
  your ad spend ÷ intents (spend comes from the ad platform).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base

SCHEMA = "experiments"

# Event types. `intent` is the money metric (clicked through a real price).
EVENT_VIEW = "view"
EVENT_INTENT = "intent"
EVENT_RESERVE = "reserve"
EVENT_TYPES = {EVENT_VIEW, EVENT_INTENT, EVENT_RESERVE}


class ExperimentEvent(Base):
    """One funnel event on a fake-door concept page. Append-only."""

    __tablename__ = "experiment_event"
    __table_args__ = {"schema": SCHEMA}

    # BIGINT on Postgres; plain INTEGER on SQLite so in-memory tests autoincrement
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    concept_slug: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str | None] = mapped_column(Text)
    # The impression as observed: client-echoed, server-filled when absent —
    # never re-derived from current config after the fact:
    price_shown: Mapped[str | None] = mapped_column(Text)  # e.g. "$29/mo"
    concept_version: Mapped[int | None] = mapped_column(BigInteger)
    candidate_ref: Mapped[str | None] = mapped_column(Text)  # joins to the selector
    session_id: Mapped[str | None] = mapped_column(Text)
    utm_source: Mapped[str | None] = mapped_column(Text)
    utm_campaign: Mapped[str | None] = mapped_column(Text)
    utm_content: Mapped[str | None] = mapped_column(Text)  # the ad creative
    referrer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# --- Concepts (the hypotheses to test) ------------------------------------


@dataclass(frozen=True)
class Tier:
    name: str  # "Pro"
    price: str  # "$29/mo"
    blurb: str
    # Honest, purchase-shaped CTA: a real price + "get early access" measures
    # willingness to pay WITHOUT implying the product is live. Clicking it logs
    # `intent` (round 1: no charge, no Stripe) and reveals the interest-gauging note.
    cta_label: str = "Get early access"
    # Stripe Payment Link. None → the email "reserve" flow (interest-gauging, no
    # charge). Only set once you're building + near-launch (an honest pre-sale).
    checkout_url: str | None = None


@dataclass(frozen=True)
class Provenance:
    """The join from a fake-door outcome back to the candidate + experiment that
    selected the concept (contract clause 6). Stored as DATA — `ref` is stamped on
    every event — so a click can label the shot features that produced it. The
    concept slug equals `slugify(shot.domain)` on the spark-swarm side (see
    shot_pipeline.py there): `candidate` is that shared key, `experiment`
    identifies the selection run."""

    experiment: str  # which selector run picked this concept
    candidate: str  # the shot's domain key (== the concept slug, by construction)
    features: dict[str, float]  # the shot features the outcome will label
    sources: tuple[str, ...]  # evidence sources behind the shot

    @property
    def ref(self) -> str:
        """The compact reference stored on each event."""
        return f"{self.experiment}#{self.candidate}"


@dataclass(frozen=True)
class Concept:
    slug: str
    name: str  # internal label for the readout
    badge: str
    headline: str
    subhead: str
    bullets: list[str]
    how_it_works: list[str]
    tiers: list[Tier]
    provenance: Provenance
    version: int = (
        1  # bump on ANY copy/pricing change — enforced by the fingerprint pin
    )
    active: bool = True
    accent: str | None = None  # optional hex for per-concept theming

    def find_tier(self, name: str | None) -> Tier | None:
        return next((t for t in self.tiers if t.name == name), None)


# ROUND 1 concepts, surfaced by the venture machine's shot-ranker (spark-swarm,
# venture-ideator-founder-fit) and bridged here via shot_pipeline.to_bof_concept.
# All three are the top GIG-FED shots: proven willingness-to-pay (people already pay
# freelancers/VAs for the manual version) AND a reachable buyer (they're on the
# marketplace). Framing is deliberate — "the thing you pay a freelancer for, automated".
# Round 1 = intent only: no checkout_url, honest "Get early access" CTA. The ad test
# picks the winner, not us.
#
# Provenance caveat: the round-1 selector is the OLD shot-ranker, which ran BEFORE the
# participation compiler was frozen and corrected. These labels will say whether gig-fed
# concepts convert; they will NOT validate the compiler, which did not select them.
# (saturation 1.0 = crowded; "take a slice with faster execution" shots, not open fields.)
_ROUND1 = "spark-swarm/venture-ideator-founder-fit:shot-ranker/round-1"

CONCEPTS: list[Concept] = [
    Concept(
        slug="document-data-extraction",
        name="Messy PDFs -> clean data",
        badge="For ops & admin teams",
        headline="Turn a pile of PDFs into a clean spreadsheet",
        subhead=(
            "Drop in forms, statements, or reports and get every field extracted, checked, "
            "and exported. The data-entry job you pay a VA for, done in minutes."
        ),
        bullets=[
            "Every field extracted and validated -- scans or native PDFs",
            "You define the columns once; we fill them on every batch",
            "Export to Excel, Google Sheets, or an API",
        ],
        how_it_works=[
            "Upload a batch of documents",
            "We extract and double-check every field",
            "Download clean, structured data",
        ],
        tiers=[
            Tier("Solo", "$29/mo", "For one person, the core workflow"),
            Tier("Business", "$79/mo", "Higher volume + exports/integrations"),
        ],
        provenance=Provenance(
            experiment=_ROUND1,
            candidate="document-data-extraction",
            features={"ev": 0.268, "wtp": 1.0, "distribution": 0.90, "saturation": 1.0},
            sources=("gigs", "n8n", "complaints"),
        ),
    ),
    Concept(
        slug="bookkeeping-invoice-automation",
        name="Invoices -> reconciled books",
        badge="For small-business owners",
        headline="Stop paying someone to reconcile your invoices",
        subhead=(
            "Forward your invoices and bank exports; get categorized, reconciled books "
            "back. The bookkeeping gig you hire out, automated."
        ),
        bullets=[
            "Reads every invoice and receipt",
            "Matches each to your ledger and bank feed",
            "Flags exactly what doesn't reconcile -- nothing silently dropped",
        ],
        how_it_works=[
            "Forward invoices + a bank export",
            "We categorize and reconcile",
            "Review the flags, export to QuickBooks or Xero",
        ],
        tiers=[
            Tier("Solo", "$29/mo", "For one person, the core workflow"),
            Tier("Business", "$79/mo", "Higher volume + exports/integrations"),
        ],
        provenance=Provenance(
            experiment=_ROUND1,
            candidate="bookkeeping-invoice-automation",
            features={"ev": 0.268, "wtp": 1.0, "distribution": 0.90, "saturation": 1.0},
            sources=("gigs", "complaints"),
        ),
    ),
    Concept(
        slug="lead-generation-research",
        name="Lead list, built for you",
        badge="For founders & sales teams",
        headline="The lead-list research you hire out, on tap",
        subhead=(
            "Describe your ideal customer and get a verified, enriched lead list back -- "
            "the VA research task, automated and always fresh."
        ),
        bullets=[
            "Finds and verifies companies + contacts matching your ICP",
            "Enriches with the fields you actually sell on",
            "Export as CSV or sync straight to your CRM",
        ],
        how_it_works=[
            "Describe your ideal customer profile",
            "We build and verify the list",
            "Export or push it to your CRM",
        ],
        tiers=[
            Tier("Solo", "$29/mo", "For one person, the core workflow"),
            Tier("Business", "$79/mo", "Higher volume + exports/integrations"),
        ],
        provenance=Provenance(
            experiment=_ROUND1,
            candidate="lead-generation-research",
            features={"ev": 0.217, "wtp": 1.0, "distribution": 0.90, "saturation": 1.0},
            sources=("gigs", "n8n", "complaints"),
        ),
    ),
]

_BY_SLUG = {c.slug: c for c in CONCEPTS}


# The shared landing-page chrome (frontend/src/ConceptLanding.tsx: "Early-access
# pricing", the interest-gauging/no-charge copy around the CTA and reserve flow)
# is part of what every visitor saw. It is pinned string-by-string in
# frontend/src/ConceptLanding.test.tsx; that pin's failure message says to bump
# THIS constant. It is folded into every concept fingerprint below, so bumping it
# forces a version bump + re-pin on every concept — chrome change = a new
# experiment everywhere.
PAGE_CHROME_VERSION = 1


def content_fingerprint(c: Concept) -> str:
    """Stable hash of everything a visitor can see on the landing page. Pinned
    per-version in tests/test_experiments.py: editing copy or pricing without
    bumping `version` fails the pin, so `version` is enforced, not aspirational."""
    payload = json.dumps(
        {
            "page_chrome": PAGE_CHROME_VERSION,
            "badge": c.badge,
            "headline": c.headline,
            "subhead": c.subhead,
            "bullets": c.bullets,
            "how_it_works": c.how_it_works,
            "tiers": [
                [t.name, t.price, t.blurb, t.cta_label, t.checkout_url] for t in c.tiers
            ],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def active_concepts() -> list[Concept]:
    return [c for c in CONCEPTS if c.active]


def get_concept(slug: str) -> Concept | None:
    c = _BY_SLUG.get(slug)
    return c if (c and c.active) else None


# --- Event logging + readout ----------------------------------------------


def log_event(
    session: Session,
    *,
    concept_slug: str,
    event_type: str,
    tier: str | None = None,
    price_shown: str | None = None,
    concept_version: int | None = None,
    session_id: str | None = None,
    utm_source: str | None = None,
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    referrer: str | None = None,
) -> bool:
    """Append one funnel event. Returns False (no raise) on a bad type so a caller
    on the hot landing-page path never breaks the render.

    `price_shown`/`concept_version` are the CLIENT'S echo of the impression it
    rendered (the page sends back what it fetched). The impression is the
    observation: if a deploy repriced the concept between page load and click, the
    echo is right and current config is wrong. An `intent` — the money metric —
    MUST carry the full echo AND name a tier of this concept: an intent whose
    price cannot be attested is rejected, never relabeled from current config.
    For view/reserve, missing fields fall back to current config only when the
    echoed version IS current (otherwise price stays NULL — honestly unknown).
    These fields are as unauthenticated as the click itself — this instruments
    our own ad traffic, it is not a ledger. `candidate_ref` is always
    server-stamped (slug-stable across versions)."""
    concept = _BY_SLUG.get(concept_slug)
    if event_type not in EVENT_TYPES or concept is None:
        return False
    price_shown = price_shown or None
    t = concept.find_tier(tier)
    if event_type == EVENT_INTENT and (
        t is None or price_shown is None or concept_version is None
    ):
        return False
    if (
        price_shown is None
        and t is not None
        and concept_version in (None, concept.version)
    ):
        price_shown = t.price
    session.add(
        ExperimentEvent(
            concept_slug=concept_slug,
            event_type=event_type,
            tier=tier,
            price_shown=price_shown,
            concept_version=concept_version if concept_version else concept.version,
            candidate_ref=concept.provenance.ref,
            session_id=session_id,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
            utm_content=utm_content,
            referrer=referrer,
        )
    )
    session.commit()
    return True


@dataclass(frozen=True)
class ConceptFunnel:
    slug: str
    name: str
    views: int
    intents: int  # the money metric
    reserves: int
    intent_rate: float  # intents / views, %


def experiment_summary(session: Session) -> list[ConceptFunnel]:
    """Per-concept funnel from the event log, ranked by intent-rate. This is what
    tells you which concept to build — highest payment-intent per view.

    Counts are UNIQUE SESSIONS per event type, not raw events: one person clicking
    the CTA twice is one intent. Events without a session_id can't be deduplicated,
    so each counts once (over-count, never collapse).

    Only events from the concept's CURRENT version count — a version bump is new
    copy/pricing, i.e. a new experiment, so the funnel resets rather than blending
    incomparable labels. Rows from before the version column existed (NULL) are
    treated as version 1, which is what was live when they were logged. Older
    versions stay in the table for direct SQL."""
    counts: dict[str, dict[str, int]] = {
        c.slug: {EVENT_VIEW: 0, EVENT_INTENT: 0, EVENT_RESERVE: 0} for c in CONCEPTS
    }
    # distinct non-null sessions + one per session-less row, in a single pass
    version = func.coalesce(ExperimentEvent.concept_version, 1)
    for slug, etype, ver, n in session.execute(
        select(
            ExperimentEvent.concept_slug,
            ExperimentEvent.event_type,
            version,
            func.count(func.distinct(ExperimentEvent.session_id))
            + func.count().filter(ExperimentEvent.session_id.is_(None)),
        ).group_by(ExperimentEvent.concept_slug, ExperimentEvent.event_type, version)
    ).all():
        c = _BY_SLUG.get(slug)
        if c is not None and int(ver) == c.version and etype in counts[slug]:
            counts[slug][etype] = int(n)

    out: list[ConceptFunnel] = []
    for c in CONCEPTS:
        cc = counts[c.slug]
        views, intents = cc[EVENT_VIEW], cc[EVENT_INTENT]
        out.append(
            ConceptFunnel(
                slug=c.slug,
                name=c.name,
                views=views,
                intents=intents,
                reserves=cc[EVENT_RESERVE],
                intent_rate=round(100.0 * intents / views, 2) if views else 0.0,
            )
        )
    out.sort(key=lambda f: (f.intent_rate, f.intents), reverse=True)
    return out
