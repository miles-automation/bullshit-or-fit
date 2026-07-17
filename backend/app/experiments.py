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
  tagged with the UTM params so each ad/channel/concept is separable. Logging is
  best-effort: a down DB must not break the landing page.
- `/exp` reads `experiment_summary` → per-concept funnel + intent-rate. Cost-per-
  intent = your ad spend ÷ intents (spend comes from the ad platform).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func, select
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    concept_slug: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    utm_source: Mapped[str | None] = mapped_column(Text)
    utm_campaign: Mapped[str | None] = mapped_column(Text)
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
class Concept:
    slug: str
    name: str  # internal label for the readout
    badge: str
    headline: str
    subhead: str
    bullets: list[str]
    how_it_works: list[str]
    tiers: list[Tier]
    active: bool = True
    accent: str | None = None  # optional hex for per-concept theming


# ROUND 1 concepts, surfaced by the venture machine's shot-ranker (spark-swarm,
# venture-ideator-founder-fit) and bridged here via shot_pipeline.to_bof_concept.
# All three are the top GIG-FED shots: proven willingness-to-pay (people already pay
# freelancers/VAs for the manual version) AND a reachable buyer (they're on the
# marketplace). Framing is deliberate — "the thing you pay a freelancer for, automated".
# Round 1 = intent only: no checkout_url, honest "Get early access" CTA. The ad test
# picks the winner, not us.
#
# Provenance (slug -> shot features), the join key for the outcome->training loop:
#   document-data-extraction        ev 0.268  wtp 1.0 dist 0.90 sat 1.0  gigs+n8n+complaints
#   bookkeeping-invoice-automation  ev 0.268  wtp 1.0 dist 0.90 sat 1.0  gigs+complaints
#   lead-generation-research        ev 0.217  wtp 1.0 dist 0.90 sat 1.0  gigs+n8n+complaints
# (sat 1.0 = crowded; these are "take a slice with faster execution" shots, not open fields.)
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
    ),
]

_BY_SLUG = {c.slug: c for c in CONCEPTS}


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
    session_id: str | None = None,
    utm_source: str | None = None,
    utm_campaign: str | None = None,
    referrer: str | None = None,
) -> bool:
    """Append one funnel event. Returns False (no raise) on a bad type so a caller
    on the hot landing-page path never breaks the render."""
    if event_type not in EVENT_TYPES or concept_slug not in _BY_SLUG:
        return False
    session.add(
        ExperimentEvent(
            concept_slug=concept_slug,
            event_type=event_type,
            tier=tier,
            session_id=session_id,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
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
    tells you which concept to build — highest payment-intent per view."""
    counts: dict[str, dict[str, int]] = {
        c.slug: {EVENT_VIEW: 0, EVENT_INTENT: 0, EVENT_RESERVE: 0} for c in CONCEPTS
    }
    for slug, etype, n in session.execute(
        select(
            ExperimentEvent.concept_slug,
            ExperimentEvent.event_type,
            func.count(),
        ).group_by(ExperimentEvent.concept_slug, ExperimentEvent.event_type)
    ).all():
        if slug in counts and etype in counts[slug]:
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
