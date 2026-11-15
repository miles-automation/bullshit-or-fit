from datetime import UTC, datetime, timedelta

import httpx

from app.jobtrends.ats import PROVIDER_ASHBY, PROVIDER_GREENHOUSE, PROVIDER_LEVER
from app.jobtrends.discovery import (
    BoardHit,
    BoardProber,
    Candidate,
    board_open_roles,
    candidate_tokens,
    due_candidates,
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


def test_board_open_roles_by_provider() -> None:
    assert board_open_roles(PROVIDER_LEVER, [{}, {}]) == 2
    assert board_open_roles(PROVIDER_LEVER, {"ok": False}) is None
    assert board_open_roles(PROVIDER_ASHBY, {"jobs": [{}]}) == 1
    assert board_open_roles(PROVIDER_GREENHOUSE, {"error": "x"}) is None


def test_due_candidates_orders_never_probed_then_oldest() -> None:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    a, b, c, d = (Candidate(s, s, 5, None) for s in "abcd")
    probed = {
        "b": now - timedelta(days=30),
        "c": now - timedelta(days=8),
        "d": now - timedelta(days=1),
    }
    assert due_candidates([d, c, b, a], probed, now) == [a, b, c]
    assert due_candidates([d, c, b, a], probed, now, limit=2) == [a, b]


def _prober(handler) -> BoardProber:  # noqa: ANN001
    return BoardProber(transport=httpx.MockTransport(handler), sleep=lambda _s: None)


def test_probe_finds_lever_board_on_name_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.lever.co" and "acmeai" in request.url.path:
            return httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
        return httpx.Response(404, json={"error": "nope"})

    hit = _prober(handler).probe(Candidate("acme-ai", "Acme AI", 9, None))
    assert hit == BoardHit(PROVIDER_LEVER, "acmeai", 2)
    assert seen[0].startswith("https://api.ashbyhq.com/posting-api/job-board/acme-ai")


def test_probe_ignores_errors_and_bad_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.ashbyhq.com":
            raise httpx.ConnectError("down")
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, text="not json")
        return httpx.Response(404)

    assert _prober(handler).probe(Candidate("x", "X", 2, None)) is None


def test_probe_accepts_empty_ashby_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.ashbyhq.com":
            return httpx.Response(200, json={"jobs": []})
        return httpx.Response(404)

    assert _prober(handler).probe(Candidate("x", "X", 2, None)) == BoardHit(
        PROVIDER_ASHBY, "x", 0
    )


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
