from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.jobtrends.ats import PROVIDER_ASHBY, PROVIDER_GREENHOUSE, PROVIDER_LEVER
from app.jobtrends.discovery import (
    BoardHit,
    BoardProber,
    Candidate,
    ProbeResult,
    ProbeState,
    board_jobs,
    candidate_tokens,
    due_candidates,
    owns_board,
    parse_yc_hiring,
)

YC = [
    {
        "slug": "acme-ai",
        "name": "Acme AI",
        "team_size": 12,
        "status": "Active",
        "isHiring": True,
        "website": "https://acme.ai",
    },
    {
        "slug": "bigco",
        "name": "BigCo",
        "team_size": 900,
        "status": "Active",
        "isHiring": True,
    },
    {
        "slug": "gone",
        "name": "Gone",
        "team_size": 5,
        "status": "Inactive",
        "isHiring": True,
    },
    {
        "slug": "nosize",
        "name": "NoSize",
        "team_size": None,
        "status": "Active",
        "isHiring": True,
    },
    {"slug": "acme-ai", "name": "Acme AI dup", "team_size": 12, "status": "Active"},
    {
        "slug": "quiet",
        "name": "Quiet",
        "team_size": 8,
        "status": "Active",
        "isHiring": False,
    },
    "junk",
]


def test_parse_yc_hiring_keeps_small_active_hiring_companies() -> None:
    out = parse_yc_hiring(YC)
    assert out == [Candidate("acme-ai", "Acme AI", 12, "https://acme.ai")]


def test_parse_yc_hiring_rejects_non_list() -> None:
    assert parse_yc_hiring({"companies": []}) == []


def test_candidate_tokens_dedupes_variants() -> None:
    assert candidate_tokens(Candidate("acme-ai", "Acme AI", 3, None)) == [
        "acme-ai",
        "acmeai",
    ]
    assert candidate_tokens(Candidate("linear", "Linear", 3, None)) == ["linear"]


def test_board_jobs_by_provider() -> None:
    assert board_jobs(PROVIDER_LEVER, [{}, {}]) == [{}, {}]
    assert board_jobs(PROVIDER_LEVER, {"ok": False}) is None
    assert board_jobs(PROVIDER_ASHBY, {"jobs": [{}]}) == [{}]
    assert board_jobs(PROVIDER_GREENHOUSE, {"error": "x"}) is None
    assert board_jobs(PROVIDER_ASHBY, {"jobs": [None]}) is None
    assert board_jobs(PROVIDER_LEVER, [{}, "x"]) is None


def test_owns_board_needs_name_or_domain_in_jobs() -> None:
    acme = Candidate("acme-ai", "Acme AI", 9, "https://www.acme.ai/")
    assert owns_board(acme, [{"descriptionPlain": "At Acme AI we build agents."}])
    assert owns_board(acme, [{"applyUrl": "https://careers.acme.ai/apply"}])
    assert not owns_board(acme, [{"descriptionPlain": "At Acmeco we sell shoes."}])
    assert not owns_board(acme, [])
    assert not owns_board(
        Candidate("acme", "Acme", 3, None),
        [
            {
                "hostedUrl": "https://jobs.lever.co/acme/123",
                "descriptionPlain": "Other Co is hiring. Apply at https://jobs.lever.co/acme/123",
            }
        ],
    )


def test_due_candidates_orders_never_probed_then_oldest() -> None:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    a, b, c, d = (Candidate(s, s, 5, None) for s in "abcd")
    state = {
        "b": ProbeState(now - timedelta(days=30), now - timedelta(days=30)),
        "c": ProbeState(now - timedelta(days=8), now - timedelta(days=8)),
        "d": ProbeState(now - timedelta(days=1), now - timedelta(days=1)),
    }
    assert due_candidates([d, c, b, a], state, now) == [a, b, c]
    assert due_candidates([d, c, b, a], state, now, limit=2) == [a, b]


def test_inconclusive_attempts_rotate_instead_of_hogging_the_budget() -> None:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    stuck, fresh, retry = (
        Candidate(s, s, 5, None) for s in ("stuck", "fresh", "retry")
    )
    state = {
        "stuck": ProbeState(None, now - timedelta(hours=2)),
        "retry": ProbeState(None, now - timedelta(days=2)),
    }
    assert due_candidates([stuck, retry, fresh], state, now) == [fresh, retry]


def _prober(handler, **kw) -> BoardProber:  # noqa: ANN001, ANN003
    return BoardProber(
        transport=httpx.MockTransport(handler), sleep=lambda _s: None, **kw
    )


ACME = Candidate("acme-ai", "Acme AI", 9, None)
ACME_JOB = {"id": "1", "descriptionPlain": "Join Acme AI."}


def test_probe_finds_owned_lever_board_on_name_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.lever.co" and "acmeai" in request.url.path:
            return httpx.Response(200, json=[ACME_JOB, ACME_JOB])
        return httpx.Response(404, json={"error": "nope"})

    result = _prober(handler).probe(ACME)
    assert result == ProbeResult(BoardHit(PROVIDER_LEVER, "acmeai", 2), True)
    assert seen[0].startswith("https://api.ashbyhq.com/posting-api/job-board/acme-ai")


def test_probe_all_404_is_conclusive_miss() -> None:
    result = _prober(lambda _r: httpx.Response(404)).probe(ACME)
    assert result == ProbeResult(None, True)


@pytest.mark.parametrize(
    "failure",
    [
        lambda: httpx.Response(429),
        lambda: httpx.Response(503),
        lambda: httpx.Response(200, text="not json"),
        lambda: httpx.Response(200, json={"error": "x"}),
    ],
)
def test_probe_errors_are_inconclusive(failure) -> None:  # noqa: ANN001
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "boards-api.greenhouse.io":
            return failure()
        return httpx.Response(404)

    assert _prober(handler).probe(ACME) == ProbeResult(None, False)


def test_probe_connection_error_is_inconclusive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert _prober(handler).probe(ACME) == ProbeResult(None, False)


def test_probe_rejects_unrelated_board_and_keeps_looking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(
                200, json={"jobs": [{"descriptionPlain": "Other Co"}]}
            )
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json={"jobs": [{"content": "We are Acme AI"}]})
        return httpx.Response(404)

    result = _prober(handler).probe(ACME)
    assert result.hit == BoardHit(PROVIDER_GREENHOUSE, "acme-ai", 1)


def test_probe_tries_stored_mapping_first_and_trusts_it() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.lever.co" and request.url.path.endswith("/acme"):
            return httpx.Response(200, json=[{"id": "1", "descriptionPlain": "Acme"}])
        return httpx.Response(404)

    renamed = Candidate("acme-ai-2", "Acme Labs", 9, None)
    result = _prober(handler).probe(renamed, (PROVIDER_LEVER, "acme"))
    assert result == ProbeResult(BoardHit(PROVIDER_LEVER, "acme", 1), True)
    assert seen == ["https://api.lever.co/v0/postings/acme?mode=json"]


def test_probe_malformed_entries_are_inconclusive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(200, json={"jobs": [None]})
        return httpx.Response(404)

    assert _prober(handler).probe(ACME) == ProbeResult(None, False)


def test_probe_stops_at_request_budget() -> None:
    prober = _prober(lambda _r: httpx.Response(404), max_requests=2)
    assert prober.probe(ACME) == ProbeResult(None, False)
    assert prober.requests == 2
    assert prober.exhausted


def test_probe_stops_at_deadline() -> None:
    ticks = iter([0.0, 100.0])
    prober = _prober(
        lambda _r: httpx.Response(404),
        max_seconds=50,
        clock=lambda: next(ticks, 100.0),
    )
    assert prober.probe(ACME) == ProbeResult(None, False)
    assert prober.requests == 0


class _Row:
    def __init__(
        self, slug: str, provider: str | None, token: str | None, found: bool
    ) -> None:
        self.company_slug = slug
        self.company_name = slug.title()
        self.team_size = 5
        self.website = None
        self.provider = provider
        self.token = token
        self.found = found
        self.probed_at = datetime(2026, 10, 1, tzinfo=UTC)
        self.attempted_at = datetime(2026, 10, 1, tzinfo=UTC)


class _Session:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.upserts = 0

    def scalars(self, _stmt):  # noqa: ANN001, ANN202
        return iter(self._rows)

    def execute(self, _stmt) -> None:  # noqa: ANN001
        self.upserts += 1

    def commit(self) -> None:
        pass


class _ScriptedProber:
    def __init__(self, results: dict[str, ProbeResult]) -> None:
        self._results = results
        self.probed: list[str] = []
        self.exhausted = False

    def probe(
        self, candidate: Candidate, stored: tuple[str, str] | None = None
    ) -> ProbeResult:
        self.probed.append(candidate.slug)
        self.stored = stored
        return self._results[candidate.slug]


def _discover(monkeypatch, rows, results, candidates):  # noqa: ANN001, ANN202
    from app.jobtrends import discovery

    closed: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        discovery,
        "close_missing",
        lambda _s, _t, *, provider, company_token, source: closed.append(
            (provider, company_token)
        ),
    )
    session = _Session(rows)
    prober = _ScriptedProber(results)
    out = discovery.discover_boards(
        session,  # type: ignore[arg-type]
        prober,  # type: ignore[arg-type]
        candidates,
        now=datetime(2026, 11, 15, tzinfo=UTC),
    )
    return out, closed, prober, session


def test_inconclusive_probe_keeps_mapping_and_closes_nothing(monkeypatch) -> None:  # noqa: ANN001
    rows = [_Row("acme", PROVIDER_LEVER, "acme", True)]
    out, closed, _p, session = _discover(
        monkeypatch,
        rows,
        {"acme": ProbeResult(None, False)},
        [Candidate("acme", "Acme", 5, None)],
    )
    assert closed == []
    assert session.upserts == 1
    assert out["inconclusive"] == 1
    assert out["probed"] == 0


def test_conclusive_miss_closes_old_board(monkeypatch) -> None:  # noqa: ANN001
    rows = [_Row("acme", PROVIDER_LEVER, "acme", True)]
    out, closed, _p, _s = _discover(
        monkeypatch,
        rows,
        {"acme": ProbeResult(None, True)},
        [Candidate("acme", "Acme", 5, None)],
    )
    assert closed == [(PROVIDER_LEVER, "acme")]
    assert out["lost"] == 1
    assert _p.stored == (PROVIDER_LEVER, "acme")


def test_board_replacement_closes_previous_board(monkeypatch) -> None:  # noqa: ANN001
    rows = [_Row("acme", PROVIDER_LEVER, "acme", True)]
    hit = ProbeResult(BoardHit(PROVIDER_ASHBY, "acme", 3), True)
    _o, closed, _p, _s = _discover(
        monkeypatch, rows, {"acme": hit}, [Candidate("acme", "Acme", 5, None)]
    )
    assert closed == [(PROVIDER_LEVER, "acme")]


def test_seeded_board_is_never_closed(monkeypatch) -> None:  # noqa: ANN001
    rows = [_Row("openai", PROVIDER_ASHBY, "openai", True)]
    _o, closed, _p, _s = _discover(
        monkeypatch,
        rows,
        {"openai": ProbeResult(None, True)},
        [Candidate("openai", "OpenAI", 5, None)],
    )
    assert closed == []


def test_found_boards_off_the_yc_list_are_still_reprobed(monkeypatch) -> None:  # noqa: ANN001
    rows = [
        _Row("gone", PROVIDER_LEVER, "gone", True),
        _Row("never", None, None, False),
    ]
    _o, closed, prober, _s = _discover(
        monkeypatch, rows, {"gone": ProbeResult(None, True)}, []
    )
    assert prober.probed == ["gone"]
    assert closed == [(PROVIDER_LEVER, "gone")]


def test_discovery_tick_snapshots_known_boards_when_yc_fetch_fails(monkeypatch) -> None:  # noqa: ANN001
    from app.jobtrends import discovery

    class _FailingProber:
        def fetch_yc_hiring(self) -> None:
            raise httpx.ConnectError("down")

    class _Session:
        rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True

    known = [discovery.Company("Acme", PROVIDER_ASHBY, "acme")]
    snapped: list[list[discovery.Company]] = []
    monkeypatch.setattr(discovery, "discovered_companies", lambda _s: known)
    monkeypatch.setattr(
        discovery,
        "ats_snapshot",
        lambda _s, _c, cos: snapped.append(cos) or {"companies_ok": 1, "open_roles": 3},
    )
    session = _Session()
    out = discovery.discovery_tick(session, _FailingProber(), object())  # type: ignore[arg-type]
    assert snapped == [known]
    assert session.rolled_back
    assert out["candidates"] == 0 and out["boards"] == 1 and out["open_roles"] == 3
