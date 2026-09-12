"""Staleness detection for the jobtrends ingest sources.

**Why this exists.** Every connector deliberately degrades a fetch failure to a
`WARNING` and skips `close_missing` (PR #24) so that a transient block can't flip
an entire source's roles to closed. That is the right call for the data — it is
why nothing was lost when USAJobs broke — but it makes a *total* outage
externally identical to a healthy run. USAJobs was dead from 2026-07-17 to
2026-08-02 (tinyproxy on the residential-egress box lost a boot race against
WireGuard); every tick logged a warning, the loop reported success, and 16 days
of federal data simply never arrived.

So: watch the data, not the code path. If a source's freshest row stops moving,
something upstream is broken regardless of which layer failed or how quietly.

**Edge-triggered.** We alert once on the ok→stale transition and once on
recovery, never every tick — `jobtrends.source_health` persists the last state we
notified on, so it survives worker restarts and container recreation.

**Never-seen sources are silent.** A source with no rows at all was never
configured (Adzuna and USAJobs both no-op without keys). We alert on
*regression*, not on absence — otherwise an unconfigured source cries wolf
forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.jobtrends.models import AtsJob, HnHiringPost, SourceHealth

logger = logging.getLogger(__name__)

STATE_OK = "ok"
STATE_STALE = "stale"

# Every continuous board re-snapshots on each tick, and the loop ticks daily, so
# 48h = two missed ticks. Long enough to ride out a single transient outage,
# short enough that a real break surfaces the next day rather than a fortnight
# later. HN is different in kind: it's a MONTHLY thread, so its rows only refresh
# when a new thread is posted — 40 days keeps month boundaries from crying wolf.
DEFAULT_MAX_AGE = timedelta(hours=48)
HN_MAX_AGE = timedelta(days=40)

SOURCE_MAX_AGE: dict[str, timedelta] = {
    "ats": DEFAULT_MAX_AGE,
    "usajobs": DEFAULT_MAX_AGE,
    "remote_board": DEFAULT_MAX_AGE,
    "adzuna": DEFAULT_MAX_AGE,
    "commute_shed": DEFAULT_MAX_AGE,
    "hn": HN_MAX_AGE,
}


@dataclass(frozen=True)
class Observation:
    """The freshest row we hold for a source. `last_seen=None` = never ingested."""

    source: str
    last_seen: datetime | None


@dataclass(frozen=True)
class Transition:
    """A state flip worth telling a human about."""

    source: str
    to_state: str
    last_seen: datetime | None
    age: timedelta | None

    @property
    def is_incident(self) -> bool:
        return self.to_state == STATE_STALE

    def message(self) -> str:
        if self.to_state == STATE_STALE:
            age = (
                "unknown age"
                if self.age is None
                else f"{self.age.total_seconds() / 3600:.0f}h"
            )
            return (
                f"jobtrends source '{self.source}' is STALE — no new rows for {age} "
                f"(last row {self.last_seen:%Y-%m-%d %H:%M UTC}). The connector is "
                f"probably failing silently; check the worker logs for a fetch WARNING."
                if self.last_seen
                else f"jobtrends source '{self.source}' is STALE — no rows at all."
            )
        return f"jobtrends source '{self.source}' RECOVERED — fresh rows are landing again."


def evaluate(
    observations: list[Observation],
    prior_states: dict[str, str],
    now: datetime,
    max_age: dict[str, timedelta] | None = None,
) -> tuple[dict[str, str], list[Transition]]:
    """Pure core: fold observations + last-notified states into new states + transitions.

    Returns `(states, transitions)`. A source is only reported when its state
    actually flips, so callers can notify unconditionally on whatever comes back.
    """
    thresholds = SOURCE_MAX_AGE if max_age is None else max_age
    states: dict[str, str] = {}
    transitions: list[Transition] = []

    for obs in observations:
        # Never ingested → not configured. Stay quiet and record nothing, so that
        # enabling the source later starts from a clean 'ok' rather than a false
        # recovery notification.
        if obs.last_seen is None:
            continue

        age = now - obs.last_seen
        limit = thresholds.get(obs.source, DEFAULT_MAX_AGE)
        state = STATE_STALE if age > limit else STATE_OK
        states[obs.source] = state

        # First sighting of a healthy source is not a recovery — seed it silently.
        prior = prior_states.get(obs.source)
        if prior is None:
            if state == STATE_STALE:
                transitions.append(Transition(obs.source, state, obs.last_seen, age))
            continue

        if prior != state:
            transitions.append(Transition(obs.source, state, obs.last_seen, age))

    return states, transitions


def observe(session: Session) -> list[Observation]:
    """Read the freshest row per source.

    `ats_jobs` holds every continuous board keyed by `source`; HN lives in its own
    raw table. `last_seen` is the right column for boards (it advances on every
    snapshot that still lists a role); HN posts are immutable, so `fetched_at`
    is when we last pulled one.
    """
    observations = [
        Observation(source=source, last_seen=last_seen)
        for source, last_seen in session.execute(
            select(AtsJob.source, func.max(AtsJob.last_seen)).group_by(AtsJob.source)
        ).all()
    ]

    hn_last = session.scalar(select(func.max(HnHiringPost.fetched_at)))
    observations.append(Observation(source="hn", last_seen=hn_last))

    # Configured-but-never-ingested sources never appear in the tables above, so
    # they are absent rather than None here. That is the same "stay quiet" case.
    return observations


def _persist(
    session: Session,
    states: dict[str, str],
    observations: list[Observation],
    now: datetime,
) -> None:
    by_source = {o.source: o.last_seen for o in observations}
    for source, state in states.items():
        stmt = (
            pg_insert(SourceHealth)
            .values(
                source=source,
                state=state,
                last_seen_at=by_source.get(source),
                changed_at=now,
                checked_at=now,
            )
            .on_conflict_do_update(
                index_elements=[SourceHealth.source],
                set_={
                    "state": state,
                    "last_seen_at": by_source.get(source),
                    "checked_at": now,
                    # Only advance changed_at when the state actually flips, so it
                    # keeps meaning "when did this incident start".
                    "changed_at": case(
                        (SourceHealth.state != state, now),
                        else_=SourceHealth.changed_at,
                    ),
                },
            )
        )
        session.execute(stmt)
    session.commit()


def check_freshness(session: Session, now: datetime | None = None) -> list[Transition]:
    """Evaluate every source, persist the new states, and return what flipped."""
    moment = now or datetime.now(tz=UTC)
    observations = observe(session)
    prior_states = {
        row.source: row.state for row in session.scalars(select(SourceHealth)).all()
    }
    states, transitions = evaluate(observations, prior_states, moment)
    _persist(session, states, observations, moment)
    return transitions


def notify(transitions: list[Transition], *, client: httpx.Client | None = None) -> int:
    """Post each transition to Spark Swarm; `incident` events fan out to Matrix.

    Spark Swarm sends the ops-room message itself whenever an `incident` event is
    written (`crud.create_event`), so posting the event IS the notification —
    there's no second Matrix credential to carry here.

    Returns the number posted. Never raises: a monitoring failure must not take
    down the ingest loop it is monitoring.
    """
    api_key = settings.bullshit_or_fit_ss_api_key
    if not api_key:
        if transitions:
            logger.warning(
                "jobtrends: %s source-health transition(s) detected but "
                "BULLSHIT_OR_FIT_SS_API_KEY is unset — not notifying",
                len(transitions),
            )
        return 0

    sent = 0
    owned = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        for t in transitions:
            payload = {
                "type": "incident" if t.is_incident else "status_change",
                "message": t.message(),
                "actor": "jobtrends-freshness",
                "metadata": {
                    "source": t.source,
                    "state": t.to_state,
                    "last_seen": t.last_seen.isoformat() if t.last_seen else None,
                },
            }
            try:
                resp = http.post(
                    f"{settings.spark_swarm_api_url}/events",
                    json=payload,
                    headers={"X-API-Key": api_key},
                )
                resp.raise_for_status()
                sent += 1
                logger.info(
                    "jobtrends: notified source-health %s for '%s'",
                    t.to_state,
                    t.source,
                )
            except Exception:  # noqa: BLE001 — monitoring must never break ingestion
                logger.exception(
                    "jobtrends: failed to post source-health event for '%s'", t.source
                )
    finally:
        if owned:
            http.close()
    return sent


def check_and_notify(session: Session, now: datetime | None = None) -> list[Transition]:
    """Convenience entry point for the worker: evaluate, persist, notify."""
    transitions = check_freshness(session, now)
    if transitions:
        notify(transitions)
    return transitions
