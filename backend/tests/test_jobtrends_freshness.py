from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.jobtrends.freshness import (
    STATE_OK,
    STATE_STALE,
    Observation,
    Transition,
    evaluate,
    notify,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _obs(source: str, hours_ago: float | None) -> Observation:
    last = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
    return Observation(source=source, last_seen=last)


def test_fresh_source_with_no_history_seeds_silently() -> None:
    states, transitions = evaluate([_obs("ats", 1)], {}, NOW)
    assert states == {"ats": STATE_OK}
    assert transitions == []


def test_stale_source_with_no_history_does_alert() -> None:
    states, transitions = evaluate([_obs("usajobs", 400)], {}, NOW)
    assert states == {"usajobs": STATE_STALE}
    assert [t.source for t in transitions] == ["usajobs"]
    assert transitions[0].is_incident


def test_ok_to_stale_raises_an_incident() -> None:
    _, transitions = evaluate([_obs("usajobs", 72)], {"usajobs": STATE_OK}, NOW)
    assert len(transitions) == 1
    assert transitions[0].to_state == STATE_STALE
    assert transitions[0].is_incident


def test_stale_to_ok_reports_recovery_not_incident() -> None:
    _, transitions = evaluate([_obs("usajobs", 1)], {"usajobs": STATE_STALE}, NOW)
    assert len(transitions) == 1
    assert transitions[0].to_state == STATE_OK
    assert not transitions[0].is_incident


def test_still_stale_does_not_re_alert() -> None:
    states, transitions = evaluate(
        [_obs("usajobs", 400)], {"usajobs": STATE_STALE}, NOW
    )
    assert states == {"usajobs": STATE_STALE}
    assert transitions == []


def test_still_ok_is_silent() -> None:
    _, transitions = evaluate([_obs("ats", 2)], {"ats": STATE_OK}, NOW)
    assert transitions == []


def test_never_ingested_source_is_ignored_entirely() -> None:
    states, transitions = evaluate([_obs("adzuna", None)], {}, NOW)
    assert states == {}
    assert transitions == []


def test_thresholds_are_per_source() -> None:
    states, _ = evaluate([_obs("ats", 240), _obs("hn", 240)], {}, NOW)
    assert states["ats"] == STATE_STALE
    assert states["hn"] == STATE_OK


def test_boundary_is_exclusive() -> None:
    states, _ = evaluate([_obs("ats", 48)], {}, NOW)
    assert states["ats"] == STATE_OK
    states, _ = evaluate([_obs("ats", 48.1)], {}, NOW)
    assert states["ats"] == STATE_STALE


def test_sources_are_evaluated_independently() -> None:
    states, transitions = evaluate(
        [_obs("ats", 1), _obs("usajobs", 400), _obs("remote_board", 1)],
        {"ats": STATE_OK, "usajobs": STATE_OK, "remote_board": STATE_STALE},
        NOW,
    )
    assert states == {"ats": STATE_OK, "usajobs": STATE_STALE, "remote_board": STATE_OK}
    flips = {t.source: t.to_state for t in transitions}
    assert flips == {"usajobs": STATE_STALE, "remote_board": STATE_OK}


def test_incident_message_names_the_source_and_age() -> None:
    t = Transition(
        "usajobs", STATE_STALE, NOW - timedelta(hours=384), timedelta(hours=384)
    )
    msg = t.message()
    assert "usajobs" in msg
    assert "STALE" in msg
    assert "384h" in msg


def test_recovery_message_is_distinct() -> None:
    t = Transition("usajobs", STATE_OK, NOW, timedelta(hours=1))
    assert "RECOVERED" in t.message()


def _capture(calls: list[httpx.Request], status: int = 201) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"id": 42})
        calls.append(request)
        return httpx.Response(status, json={"id": "evt_1"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_notify_posts_incident_for_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobtrends import freshness

    monkeypatch.setattr(
        freshness.settings, "bullshit_or_fit_ss_api_key", "k", raising=False
    )
    calls: list[httpx.Request] = []
    with _capture(calls) as client:
        sent = notify(
            [Transition("usajobs", STATE_STALE, NOW, timedelta(hours=400))],
            client=client,
        )

    assert sent == 1
    assert calls[0].url.path.endswith("/events")
    assert calls[0].headers["X-API-Key"] == "k"
    import json

    payload = json.loads(calls[0].content)
    assert payload["type"] == "incident"
    assert payload["spark_id"] == 42
    assert json.loads(payload["metadata"])["source"] == "usajobs"


def test_notify_posts_status_change_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.jobtrends import freshness

    monkeypatch.setattr(
        freshness.settings, "bullshit_or_fit_ss_api_key", "k", raising=False
    )
    calls: list[httpx.Request] = []
    with _capture(calls) as client:
        notify(
            [Transition("usajobs", STATE_OK, NOW, timedelta(hours=1))], client=client
        )

    import json

    assert json.loads(calls[0].content)["type"] == "status_change"


def test_notify_without_api_key_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobtrends import freshness

    monkeypatch.setattr(
        freshness.settings, "bullshit_or_fit_ss_api_key", "", raising=False
    )
    calls: list[httpx.Request] = []
    with _capture(calls) as client:
        sent = notify(
            [Transition("usajobs", STATE_STALE, NOW, timedelta(hours=400))],
            client=client,
        )

    assert sent == 0
    assert calls == []


def test_notify_swallows_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobtrends import freshness

    monkeypatch.setattr(
        freshness.settings, "bullshit_or_fit_ss_api_key", "k", raising=False
    )

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("spark swarm unreachable")

    with httpx.Client(transport=httpx.MockTransport(boom)) as client:
        sent = notify(
            [Transition("usajobs", STATE_STALE, NOW, timedelta(hours=400))],
            client=client,
        )

    assert sent == 0


def test_notify_continues_after_one_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.jobtrends import freshness

    monkeypatch.setattr(
        freshness.settings, "bullshit_or_fit_ss_api_key", "k", raising=False
    )
    seen: list[str] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        import json

        if request.method == "GET":
            return httpx.Response(200, json={"id": 42})
        source = json.loads(json.loads(request.content)["metadata"])["source"]
        seen.append(source)
        if source == "usajobs":
            raise httpx.ConnectError("boom")
        return httpx.Response(201, json={"id": "evt"})

    with httpx.Client(transport=httpx.MockTransport(flaky)) as client:
        sent = notify(
            [
                Transition("usajobs", STATE_STALE, NOW, timedelta(hours=400)),
                Transition("ats", STATE_STALE, NOW, timedelta(hours=400)),
            ],
            client=client,
        )

    assert seen == ["usajobs", "ats"]
    assert sent == 1


def test_failed_notifications_retry_until_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import cast
    from unittest.mock import MagicMock

    from sqlalchemy.orm import Session

    from app.jobtrends import freshness

    prior = {"usajobs": STATE_OK}
    session = MagicMock()
    session.scalars.return_value.all.side_effect = lambda: [
        freshness.SourceHealth(source=source, state=state)
        for source, state in prior.items()
    ]
    monkeypatch.setattr(freshness, "observe", lambda session: [_obs("usajobs", 72)])
    outcomes = iter([0, 1])
    notify_mock = MagicMock(side_effect=lambda transitions: next(outcomes))
    monkeypatch.setattr(freshness, "notify", notify_mock)

    def persist(
        session: Session,
        states: dict[str, str],
        observations: list[Observation],
        now: datetime,
    ) -> None:
        prior.update(states)

    monkeypatch.setattr(freshness, "_persist", persist)
    freshness.check_and_notify(cast(Session, session), NOW)
    assert prior == {"usajobs": STATE_OK}
    freshness.check_and_notify(cast(Session, session), NOW)
    assert prior == {"usajobs": STATE_STALE}
    freshness.check_and_notify(cast(Session, session), NOW)
    assert notify_mock.call_count == 2
