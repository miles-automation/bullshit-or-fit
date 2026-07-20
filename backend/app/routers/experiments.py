"""API for the concept-testing harness: serve concepts, log funnel events, read out.

The landing page (`/c/{slug}`) fetches its concept here, logs a `view` on load and
an `intent` when a visitor clicks a priced CTA (then goes to Stripe checkout). `/exp`
reads the per-concept funnel. Event logging is best-effort — a DB hiccup returns
`{ok: false}`, never a 500, so the ad-funded landing page never breaks.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.experiments import (
    active_concepts,
    experiment_summary,
    get_concept,
    log_event,
)

router = APIRouter(prefix="/exp", tags=["experiments"])


class TierOut(BaseModel):
    name: str
    price: str
    blurb: str
    cta_label: str
    checkout_url: str | None


class ConceptOut(BaseModel):
    slug: str
    name: str
    badge: str
    headline: str
    subhead: str
    bullets: list[str]
    how_it_works: list[str]
    tiers: list[TierOut]
    accent: str | None


class ConceptCardOut(BaseModel):
    slug: str
    name: str
    headline: str


class EventIn(BaseModel):
    event_type: str  # view | intent | reserve
    tier: str | None = None
    session_id: str | None = None
    utm_source: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None  # the ad creative
    referrer: str | None = None


class EventOut(BaseModel):
    ok: bool


class FunnelOut(BaseModel):
    slug: str
    name: str
    views: int
    intents: int
    reserves: int
    intent_rate: float


class SummaryOut(BaseModel):
    concepts: list[FunnelOut]


def _concept_out(c) -> ConceptOut:  # noqa: ANN001 — internal Concept dataclass
    return ConceptOut(
        slug=c.slug,
        name=c.name,
        badge=c.badge,
        headline=c.headline,
        subhead=c.subhead,
        bullets=c.bullets,
        how_it_works=c.how_it_works,
        tiers=[TierOut(**t.__dict__) for t in c.tiers],
        accent=c.accent,
    )


@router.get("/concepts", response_model=list[ConceptCardOut])
def list_concepts() -> list[ConceptCardOut]:
    return [
        ConceptCardOut(slug=c.slug, name=c.name, headline=c.headline)
        for c in active_concepts()
    ]


@router.get("/concepts/{slug}", response_model=ConceptOut)
def read_concept(slug: str) -> ConceptOut:
    from fastapi import HTTPException

    c = get_concept(slug)
    if c is None:
        raise HTTPException(status_code=404, detail="Unknown concept")
    return _concept_out(c)


@router.post("/{slug}/event", response_model=EventOut)
def record_event(
    slug: str, payload: EventIn, db: Session = Depends(get_db)
) -> EventOut:
    """Best-effort funnel-event logging. Never 500s the landing page."""
    try:
        ok = log_event(
            db,
            concept_slug=slug,
            event_type=payload.event_type,
            tier=payload.tier,
            session_id=payload.session_id,
            utm_source=payload.utm_source,
            utm_campaign=payload.utm_campaign,
            utm_content=payload.utm_content,
            referrer=payload.referrer,
        )
    except Exception:  # noqa: BLE001 — event logging must not break the ad page
        db.rollback()
        ok = False
    return EventOut(ok=ok)


@router.get("/summary", response_model=SummaryOut)
def summary(db: Session = Depends(get_db)) -> SummaryOut:
    return SummaryOut(
        concepts=[FunnelOut(**f.__dict__) for f in experiment_summary(db)]
    )
