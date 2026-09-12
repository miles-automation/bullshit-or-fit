from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.jobtrends.sec import client as client_module
from app.jobtrends.sec import service
from app.jobtrends.sec.client import SecClient
from app.jobtrends.sec.discovery import discover, filing_rows
from app.jobtrends.sec.models import SecDocument, SecFiling, SecSignal
from app.jobtrends.sec.registry import Issuer, load_registry, unmatched_employers
from app.jobtrends.sec.service import ingest, rebuild, report
from app.jobtrends.sec.text import extract, load_rules, normalize, paragraphs

ISSUER = Issuer(
    name="Test Company",
    cik="0000000001",
    verification_url="https://data.sec.gov/submissions/CIK0000000001.json",
)
BODY = b"""<html><body><h2>Item 7. Management Discussion</h2>
<p>During 2025 we completed a restructuring and reduced our workforce.</p>
<p>We plan to increase capital expenditures to expand our facilities.</p>
<p>Our business serves many customers in several regions. The results described here relate to the reporting period and may differ from current business conditions.</p></body></html>"""


def submissions(rows: list[tuple[str, str, str, str, str]]) -> dict[str, Any]:
    return dict(
        zip(
            ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument"),
            [list(column) for column in zip(*rows, strict=True)],
            strict=True,
        )
    )


ROWS = [
    ("0000000001-25-000001", "10-K", "2025-02-01", "2024-12-31", "annual.htm"),
    ("0000000001-26-000001", "10-K", "2026-02-01", "2025-12-31", "annual.htm"),
    ("0000000001-26-000002", "10-K/A", "2026-03-01", "2025-12-31", "amend.htm"),
]


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", execution_options={"schema_translate_map": {"jobtrends": None}}
    )
    for model in (SecFiling, SecDocument, SecSignal):
        model.__table__.create(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


@pytest.fixture(autouse=True)
def no_real_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module.time, "sleep", lambda seconds: None)


def test_replay_amendment_rebuild_and_provenance(
    session: Session, tmp_path: Path
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "data.sec.gov":
            return httpx.Response(
                200,
                json={
                    "cik": "1",
                    "filings": {"recent": submissions(ROWS), "files": []},
                },
            )
        return httpx.Response(200, content=BODY)

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        first = ingest(session, client, [ISSUER], load_rules())
        second = ingest(session, client, [ISSUER], load_rules())
        assert first["downloaded"] == 3
        assert second["downloaded"] == 0
        assert second["cached"] == 3
        assert len(requests) == 4
        assert session.scalar(select(func.count()).select_from(SecFiling)) == 3
        amendment = session.get(SecFiling, ROWS[2][0])
        assert amendment is not None
        assert amendment.original_accession == ROWS[1][0]
        assert amendment.form == "10-K/A"
        initial_count = session.scalar(select(func.count()).select_from(SecSignal))
        assert initial_count == 6
        summary = rebuild(session, load_rules())
        assert summary == {"filings": 3, "signals": 6, "failed": 0}
        assert len(requests) == 4
        assert (
            session.scalar(select(func.count()).select_from(SecSignal)) == initial_count
        )
        bounded = report(session, form="10-K", until=date(2025, 12, 31))
        assert {row["accession"] for row in bounded["signals"]} == {ROWS[0][0]}
        result = report(
            session, since=date(2026, 1, 1), category="workforce_restructuring", limit=1
        )
        assert result["truncated"] is True
        for signal in result["signals"]:
            document = session.get(SecDocument, signal["accession"])
            assert document is not None
            assert (
                document.normalized_text[signal["start_offset"] : signal["end_offset"]]
                == signal["excerpt"]
            )
            assert signal["event_date"] is None
            assert "2025" in signal["excerpt"]
            assert signal["content_hash"]
            assert signal["source_url"].startswith("https://www.sec.gov/Archives/")


def test_failed_download_is_visible_and_resumes(
    session: Session, tmp_path: Path
) -> None:
    failures = True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.sec.gov":
            return httpx.Response(
                200,
                json={"cik": 1, "filings": {"recent": submissions(ROWS), "files": []}},
            )
        if "26-000001".replace("-", "") in request.url.path and failures:
            return httpx.Response(404)
        return httpx.Response(200, content=BODY)

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        first = ingest(session, client, [ISSUER], load_rules())
        assert first["failed"] == 1
        assert first["downloaded"] == 2
        failed = session.get(SecFiling, ROWS[1][0])
        assert failed is not None and failed.fetch_error
        failures = False
        second = ingest(session, client, [ISSUER], load_rules())
        assert second["failed"] == 0
        assert second["downloaded"] == 1
        assert failed.fetch_error is None


def test_invalid_document_preserves_raw_and_reports_parse_failure(
    session: Session, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.sec.gov":
            return httpx.Response(
                200,
                json={
                    "cik": 1,
                    "filings": {"recent": submissions(ROWS[:1]), "files": []},
                },
            )
        return httpx.Response(200, content=b"not an annual report")

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        result = ingest(session, client, [ISSUER], load_rules())
    assert result["failed"] == 1
    filing = session.get(SecFiling, ROWS[0][0])
    document = session.get(SecDocument, ROWS[0][0])
    assert filing is not None and filing.raw_document == b"not an annual report"
    assert document is not None and document.parse_status == "failed"
    assert document.section_status == "unknown"
    assert report(session)["signals"] == []


def test_discovery_loads_archive_and_keeps_original_periods(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "submissions-" in request.url.path:
            return httpx.Response(200, json=submissions(ROWS[:1]))
        return httpx.Response(
            200,
            json={
                "cik": 1,
                "filings": {
                    "recent": submissions(ROWS[1:]),
                    "files": [
                        {
                            "name": "CIK0000000001-submissions-001.json",
                            "filingTo": "2025-05-01",
                        }
                    ],
                },
            },
        )

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        refs, warnings = discover(client, ISSUER, 2)
    assert len(refs) == 3
    assert warnings == []
    assert [ref.form for ref in refs] == ["10-K", "10-K", "10-K/A"]


def test_malformed_arrays_and_unsafe_primary_documents_fail() -> None:
    data = submissions(ROWS)
    data["reportDate"].pop()
    with pytest.raises(ValueError, match="mismatched"):
        filing_rows(data)
    data = submissions(ROWS)
    data["primaryDocument"][0] = "../../private"
    with pytest.raises(ValueError):
        filing_rows(data)


def test_retry_cache_and_shared_throttle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", waits.append)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert "test@example.com" in request.headers["User-Agent"]
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        assert client.json("https://data.sec.gov/test") == {"ok": True}
        assert client.json("https://data.sec.gov/test") == {"ok": True}
        assert client.cache_hits == 1
    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as another:
        another.fetch("https://data.sec.gov/another")
    assert calls == 3
    assert 7 in waits
    assert any(0 < wait <= 0.5 for wait in waits)
    assert (tmp_path / "request.lock").read_text()


def test_retries_are_bounded_and_reject_non_sec_urls(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch("https://www.sec.gov/test")
        with pytest.raises(ValueError, match="official"):
            client.fetch("https://attacker.invalid/doc")
    assert calls == 4
    with pytest.raises(ValueError, match="authorized contact"):
        SecClient("", tmp_path)


def test_response_size_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 5)
    with SecClient(
        "test@example.com",
        tmp_path,
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"123456")),
    ) as client:
        with pytest.raises(ValueError, match="limit"):
            client.fetch("https://www.sec.gov/test")


def test_parser_preserves_boundaries_tables_and_ignores_hidden_data() -> None:
    raw = BODY.replace(
        b"</body>",
        b"<table><tr><td>Revenue</td><td>$100</td></tr><tr><td>2025</td><td>$90</td></tr></table><script>layoff secret</script><ix:hidden>hidden facts</ix:hidden><p>Visible <b>inline</b> text.</p></body>",
    )
    text = normalize(raw)
    assert "Revenue | $100\n\n2025 | $90" in text
    assert "Visible inline text." in text
    assert "secret" not in text and "hidden facts" not in text
    assert all(text[p.start : p.end] == p.text for p in paragraphs(text))
    assert paragraphs(text)[1].section == "Item 7: Management Discussion"


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("We completed a workforce reduction during 2023.", "reported_event"),
        ("We may undertake layoffs if demand weakens.", "hypothetical_risk"),
        (
            "We plan to undertake workforce restructuring next year.",
            "forward_looking_intention",
        ),
        ("We did not implement layoffs this year.", "negated"),
        ("Workforce restructuring costs are discussed in the table.", "unknown"),
        (
            "We completed a restructuring. We may undertake additional layoffs.",
            "unknown",
        ),
    ],
)
def test_assertions_preserve_uncertainty(sentence: str, expected: str) -> None:
    signals = extract(sentence, load_rules())
    assert len(signals) == 1
    assert signals[0].assertion == expected


def test_risk_boilerplate_not_reported_and_mixed_categories_are_separate() -> None:
    rules = load_rules()
    risk = extract(
        "Item 1A. Risk Factors\n\nWe experienced issues retaining key employees.", rules
    )
    assert risk[0].assertion == "unknown"
    mixed = extract(
        "We completed restructuring in 2024. Competition for skilled employees could increase.",
        rules,
    )
    assert {signal.category: signal.assertion for signal in mixed} == {
        "workforce_restructuring": "reported_event",
        "hiring_retention_constraints": "hypothetical_risk",
    }
    assert extract("We deliver software to customers around the world.", rules) == []


def test_custom_rule_fingerprint_changes_with_pattern(tmp_path: Path) -> None:
    rules = load_rules()
    first = rules.fingerprint
    rules.rules[0].pattern = "other words"
    assert rules.fingerprint != first
    path = tmp_path / "rules.json"
    path.write_text(rules.model_dump_json())
    assert load_rules(str(path)).fingerprint == rules.fingerprint


def test_registry_has_explicit_mapping_and_preserves_unmatched() -> None:
    issuers = load_registry()
    assert len(issuers) == 10
    assert len({issuer.cik for issuer in issuers}) == 10
    assert all(issuer.provider == "greenhouse" for issuer in issuers)
    assert any(row["name"] == "Anthropic" for row in unmatched_employers(issuers))


def test_disabled_and_failed_refresh_do_not_escape(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(service, "SecClient", unavailable)
    monkeypatch.setattr(service.settings, "sec_enabled", False)
    service.refresh_if_enabled()
    assert "source unavailable" not in caplog.text
    monkeypatch.setattr(service.settings, "sec_enabled", True)
    service.refresh_if_enabled()
    assert "other sources remain independent" in caplog.text


def test_download_budget_resumes_past_cached_filings(
    session: Session, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.sec.gov":
            return httpx.Response(
                200,
                json={"cik": 1, "filings": {"recent": submissions(ROWS), "files": []}},
            )
        return httpx.Response(200, content=BODY)

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        for expected in range(1, 4):
            result = ingest(session, client, [ISSUER], load_rules(), max_filings=1)
            assert result["downloaded"] == 1
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SecFiling)
                    .where(SecFiling.raw_document.is_not(None))
                )
                == expected
            )
        assert (
            ingest(session, client, [ISSUER], load_rules(), max_filings=1)[
                "download_attempts"
            ]
            == 0
        )


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("We do not expect layoffs this year.", "negated"),
        (
            "If we do not comply with regulations, we could face workforce restructuring.",
            "hypothetical_risk",
        ),
        (
            "Employees are at-will and restructuring costs are discussed below.",
            "unknown",
        ),
        (
            "Accrued expenses increased due to changes in our workforce restructuring liability.",
            "unknown",
        ),
        (
            "In February 2023, we announced a workforce reduction plan. Restructuring charges were $10 million.",
            "reported_event",
        ),
    ],
)
def test_real_sample_classification_regressions(sentence: str, expected: str) -> None:
    assert extract(sentence, load_rules())[0].assertion == expected


@pytest.mark.parametrize(
    "sentence",
    [
        "Our debt restructuring may require negotiating with creditors.",
        "Regulators could require restructuring of our products.",
        "Dr. Lakhani was selected because of his expertise in artificial intelligence and company strategy.",
    ],
)
def test_non_workforce_restructuring_and_biography_are_not_signals(
    sentence: str,
) -> None:
    assert extract(sentence, load_rules()) == []


@pytest.mark.parametrize(
    ("sentence", "category"),
    [
        (
            "We have experienced difficulties hiring employees and may struggle to attract and retain qualified personnel.",
            "hiring_retention_constraints",
        ),
        (
            "While we expect to continue to expand our operations, our growth may not be sustainable.",
            "expansion_capital_investment",
        ),
        (
            "We announced a workforce reduction that may reduce costs.",
            "workforce_restructuring",
        ),
    ],
)
def test_mixed_assertions_remain_unknown(sentence: str, category: str) -> None:
    assert {row.category: row.assertion for row in extract(sentence, load_rules())}[
        category
    ] == "unknown"


def test_accounting_exclusion_does_not_negate_workforce_event() -> None:
    sentence = "Adjusted EBITDA does not include restructuring costs related to employee severance."
    assert extract(sentence, load_rules())[0].assertion == "unknown"


@pytest.mark.parametrize(
    "timestamp", ["2600000.0", "torn-write", "nan", "inf", "-inf", ""]
)
def test_stale_or_corrupt_shared_lock_repairs_with_bounded_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timestamp: str
) -> None:
    waits: list[float] = []
    monkeypatch.setattr(client_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(client_module.time, "sleep", waits.append)
    lock = tmp_path / "request.lock"
    lock.write_text(timestamp)
    with SecClient(
        "test@example.com",
        tmp_path,
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok")),
    ) as client:
        assert client.fetch("https://www.sec.gov/test") == b"ok"
    assert all(0 <= wait <= 0.5 for wait in waits)
    assert float(lock.read_text()) == 100.0


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_throttle_interval_rejected(tmp_path: Path, interval: float) -> None:
    with pytest.raises(ValueError):
        SecClient("test@example.com", tmp_path, interval_seconds=interval)


@pytest.mark.parametrize(
    "sentence",
    [
        "Actual or perceived breaches could negatively affect our ability to attract and retain new customers.",
        "We employ 100 employees. Uptime affects our ability to attract and retain customers.",
    ],
)
def test_customer_retention_is_not_workforce_retention(sentence: str) -> None:
    assert extract(sentence, load_rules()) == []


def test_historical_workforce_reductions_with_future_growth_are_mixed() -> None:
    sentence = "Although we have conducted workforce reductions in the past, we may experience employee growth in the future."
    assert extract(sentence, load_rules())[0].assertion == "unknown"
    assert (
        extract("We have conducted workforce reductions in the past.", load_rules())[
            0
        ].assertion
        == "reported_event"
    )
    assert (
        extract(
            "We may be unable to attract and retain highly qualified personnel.",
            load_rules(),
        )[0].category
        == "hiring_retention_constraints"
    )


def test_unseen_issuer_progresses_ahead_of_permanent_retry(
    session: Session, tmp_path: Path
) -> None:
    other = Issuer(
        name="Other Company",
        cik="0000000002",
        verification_url="https://data.sec.gov/submissions/CIK0000000002.json",
    )
    other_rows = [
        ("0000000002-25-000001", "10-K", "2025-02-01", "2024-12-31", "annual.htm")
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.sec.gov":
            second = "CIK0000000002" in request.url.path
            return httpx.Response(
                200,
                json={
                    "cik": 2 if second else 1,
                    "filings": {
                        "recent": submissions(other_rows if second else ROWS[:1]),
                        "files": [],
                    },
                },
            )
        return (
            httpx.Response(200, content=BODY)
            if "/data/2/" in request.url.path
            else httpx.Response(404)
        )

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        first = ingest(session, client, [ISSUER, other], load_rules(), max_filings=1)
        assert first["failed"] == 1
        second = ingest(session, client, [ISSUER, other], load_rules(), max_filings=1)
        assert second["downloaded"] == 1
        assert second["failed"] == 0
        assert any(
            row["cik"] == other.cik and row["bytes"] > 0 for row in second["filings"]
        )


def test_failed_metadata_refreshes_but_successful_provenance_is_frozen(
    session: Session, tmp_path: Path
) -> None:
    current_row = (ROWS[0][0], "10-K", "2025-02-01", "2024-12-31", "wrong.htm")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.sec.gov":
            return httpx.Response(
                200,
                json={
                    "cik": 1,
                    "filings": {"recent": submissions([current_row]), "files": []},
                },
            )
        return (
            httpx.Response(200, content=BODY)
            if request.url.path.endswith("correct.htm")
            else httpx.Response(404)
        )

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        assert ingest(session, client, [ISSUER], load_rules())["failed"] == 1
        for path in tmp_path.glob("*.json"):
            path.unlink()
        current_row = (ROWS[0][0], "10-K", "2025-02-02", "2024-12-30", "correct.htm")
        assert ingest(session, client, [ISSUER], load_rules())["downloaded"] == 1
        filing = session.get(SecFiling, ROWS[0][0])
        assert filing is not None
        assert filing.source_url.endswith("correct.htm")
        assert filing.filing_date == date(2025, 2, 2)
        assert filing.report_date == date(2024, 12, 30)
        for path in tmp_path.glob("*.json"):
            path.unlink()
        current_row = (ROWS[0][0], "10-K", "2025-02-03", "2024-12-31", "later.htm")
        assert ingest(session, client, [ISSUER], load_rules())["downloaded"] == 0
        assert filing.source_url.endswith("correct.htm")
        assert filing.filing_date == date(2025, 2, 2)


def test_replay_does_not_transfer_raw_or_normalized_payloads(
    session: Session, tmp_path: Path
) -> None:
    from sqlalchemy import event

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.sec.gov":
            return httpx.Response(
                200,
                json={"cik": 1, "filings": {"recent": submissions(ROWS), "files": []}},
            )
        return httpx.Response(200, content=BODY)

    columns: list[str] = []

    def capture(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        columns.extend(column[0] for column in cursor.description or [])

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        ingest(session, client, [ISSUER], load_rules())
        session.expunge_all()
        engine = session.get_bind()
        event.listen(engine, "after_cursor_execute", capture)
        try:
            assert ingest(session, client, [ISSUER], load_rules())["cached"] == 3
        finally:
            event.remove(engine, "after_cursor_execute", capture)
    assert not any(
        name.endswith(("raw_document", "normalized_text")) for name in columns
    )


def test_cli_uses_ingest_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace
    from app.jobtrends.sec import cli

    captured: list[tuple[int, int]] = []

    def capture(
        session: Any,
        client: Any,
        issuers: Any,
        rules: Any,
        periods: int,
        max_filings: int,
    ) -> dict[str, int]:
        captured.append((periods, max_filings))
        return {"failed": 0}

    monkeypatch.setattr(cli.settings, "sec_periods", 3)
    monkeypatch.setattr(cli.settings, "sec_max_filings", 1)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(
        cli,
        "SecClient",
        lambda *args: nullcontext(SimpleNamespace(requests=0, cache_hits=0)),
    )
    monkeypatch.setattr(cli, "ingest", capture)
    assert cli.main(["ingest"]) == 0
    assert captured == [(3, 1)]


def test_worker_continues_derived_rebuild_when_sec_import_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import builtins
    from contextlib import nullcontext
    from app.jobtrends import worker

    real_import = builtins.__import__
    rebuilt: list[bool] = []

    def unavailable_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("app.jobtrends."):
            raise ImportError("test source unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(worker, "HNAlgoliaClient", lambda: None)
    monkeypatch.setattr(worker, "ingest_recent", lambda *args: {})
    monkeypatch.setattr(worker, "rebuild_derived", lambda session: rebuilt.append(True))
    monkeypatch.setattr(builtins, "__import__", unavailable_import)
    worker._run_once(1)
    assert rebuilt == [True]
    assert "SEC source unavailable; continuing other sources" in caplog.text


def test_discovery_reports_history_cap_and_incomplete_periods(tmp_path: Path) -> None:
    files = [
        {
            "name": f"CIK0000000001-submissions-{index:03d}.json",
            "filingTo": "2026-05-01",
        }
        for index in range(22)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "submissions-" in request.url.path:
            return httpx.Response(200, json=submissions(ROWS[1:2]))
        return httpx.Response(
            200,
            json={
                "cik": 1,
                "filings": {"recent": submissions(ROWS[1:2]), "files": files},
            },
        )

    with SecClient(
        "test@example.com", tmp_path, httpx.MockTransport(handler)
    ) as client:
        refs, warnings = discover(client, ISSUER, 2)
        assert client.requests == 21
    assert len(refs) == 1
    assert any("20 files" in warning for warning in warnings)
    assert any("1 original annual" in warning for warning in warnings)
