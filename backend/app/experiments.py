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


# NOTE: these are DRAFT placeholders to make the harness runnable — deliberately
# diverse, non-job-related, each aimed at a buyer with a company card. Swap in the
# real hypotheses; the ad test picks the winner, not us.
CONCEPTS: list[Concept] = [
    Concept(
        slug="tidyreceipts",
        name="Receipts → clean spreadsheet",
        badge="For small-business owners",
        headline="Turn a shoebox of receipts into a clean spreadsheet",
        subhead=(
            "Forward or snap your receipts and invoices; get a tidy, categorized "
            "spreadsheet (or QuickBooks-ready export) back in minutes. No manual entry."
        ),
        bullets=[
            "Snap, email, or drop a PDF — we read it",
            "Vendor, date, amount, category, tax — extracted and checked",
            "Export to Excel, Google Sheets, or QuickBooks",
        ],
        how_it_works=[
            "Send receipts (photo, email, or upload)",
            "We extract + categorize every line",
            "Download a clean, accountant-ready sheet",
        ],
        tiers=[
            Tier("Solo", "$19/mo", "Up to 100 receipts/mo"),
            Tier("Business", "$49/mo", "Up to 500 receipts + QuickBooks export"),
        ],
    ),
    Concept(
        slug="followups",
        name="Meeting notes → follow-ups",
        badge="For consultants & account managers",
        headline="Every meeting, turned into the follow-up you forget to send",
        subhead=(
            "Drop your notes or a transcript; get the recap email, the action items, "
            "and the next-step nudge — written in your voice, ready to send."
        ),
        bullets=[
            "Recap email drafted in your tone, per client",
            "Action items with owners + due dates",
            "A follow-up reminder so nothing goes cold",
        ],
        how_it_works=[
            "Paste notes or a transcript after a call",
            "Get a send-ready recap + action list",
            "One click to copy, or send from your inbox",
        ],
        tiers=[
            Tier("Pro", "$15/mo", "Unlimited meetings, one workspace"),
            Tier("Team", "$39/mo", "Shared templates + client history"),
        ],
    ),
    Concept(
        slug="whiteboardsnap",
        name="Whiteboard photo → clean doc",
        badge="For teams & builders",
        headline="Photograph a whiteboard, get a clean editable doc",
        subhead=(
            "Snap the messy whiteboard at the end of a session; get back a structured, "
            "editable document — diagrams, lists, and decisions, digitized and shareable."
        ),
        bullets=[
            "Handwriting + boxes-and-arrows, read accurately",
            "Structured output: decisions, todos, diagram",
            "Export to Notion, Markdown, or a shared link",
        ],
        how_it_works=[
            "Take a photo of the whiteboard",
            "We digitize + structure it",
            "Edit and share in seconds",
        ],
        tiers=[
            Tier("Personal", "$9/mo", "Unlimited snaps, personal use"),
            Tier("Team", "$29/mo", "Shared workspace + integrations"),
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
