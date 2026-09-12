from __future__ import annotations

import json
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
    source: str
    last_seen: datetime | None


@dataclass(frozen=True)
class Transition:
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
    thresholds = SOURCE_MAX_AGE if max_age is None else max_age
    states: dict[str, str] = {}
    transitions: list[Transition] = []

    for obs in observations:
        if obs.last_seen is None:
            continue

        age = now - obs.last_seen
        limit = thresholds.get(obs.source, DEFAULT_MAX_AGE)
        state = STATE_STALE if age > limit else STATE_OK
        states[obs.source] = state

        prior = prior_states.get(obs.source)
        if prior is None:
            if state == STATE_STALE:
                transitions.append(Transition(obs.source, state, obs.last_seen, age))
            continue

        if prior != state:
            transitions.append(Transition(obs.source, state, obs.last_seen, age))

    return states, transitions


def observe(session: Session) -> list[Observation]:
    observations = [
        Observation(source=source, last_seen=last_seen)
        for source, last_seen in session.execute(
            select(AtsJob.source, func.max(AtsJob.last_seen)).group_by(AtsJob.source)
        ).all()
    ]

    hn_last = session.scalar(select(func.max(HnHiringPost.fetched_at)))
    observations.append(Observation(source="hn", last_seen=hn_last))

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
    moment = now or datetime.now(tz=UTC)
    observations = observe(session)
    prior_states = {
        row.source: row.state for row in session.scalars(select(SourceHealth)).all()
    }
    states, transitions = evaluate(observations, prior_states, moment)
    _persist(session, states, observations, moment)
    return transitions


def notify(transitions: list[Transition], *, client: httpx.Client | None = None) -> int:
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
        try:
            spark_response = http.get(
                f"{settings.spark_swarm_api_url}/sparks/{settings.spark_slug}",
                headers={"X-API-Key": api_key},
            )
            spark_response.raise_for_status()
            spark_id = spark_response.json()["id"]
            if type(spark_id) is not int:
                raise ValueError("invalid Spark identifier")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            logger.exception(
                "jobtrends: could not resolve Spark for freshness notification"
            )
            return 0
        for t in transitions:
            payload = {
                "spark_id": spark_id,
                "type": "incident" if t.is_incident else "status_change",
                "message": t.message(),
                "actor": "jobtrends-freshness",
                "metadata": json.dumps(
                    {
                        "source": t.source,
                        "state": t.to_state,
                        "last_seen": t.last_seen.isoformat() if t.last_seen else None,
                    }
                ),
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
    moment = now or datetime.now(tz=UTC)
    observations = observe(session)
    prior_states = {
        row.source: row.state for row in session.scalars(select(SourceHealth)).all()
    }
    states, transitions = evaluate(observations, prior_states, moment)
    for transition in transitions:
        if notify([transition]) != 1:
            states.pop(transition.source, None)
    _persist(session, states, observations, moment)
    return transitions
