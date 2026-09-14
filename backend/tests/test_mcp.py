import asyncio
import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from mcp import Client, types
from mcp.client.stdio import StdioServerParameters
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.jobtrends.models import AtsJob
from app.jobtrends.sec.models import SecDocument, SecFiling, SecSignal
from app.mcp import server as server_module
from app.mcp.queries import FilingArgs, JobArgs, SignalArgs, read_page
from app.mcp.server import build_server


@pytest.fixture
def database() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"jobtrends": None}},
    )
    for model in (SecFiling, SecDocument, SecSignal, AtsJob):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    now = datetime(2026, 9, 13, tzinfo=UTC)
    with factory() as session:
        for index in range(3):
            accession = f"0000000001-26-{index:06d}"
            session.add(
                SecFiling(
                    accession=accession,
                    cik="0000000001",
                    issuer_name="Fixture Co",
                    provider="greenhouse",
                    company_token="fixture",
                    form="10-K/A" if index == 2 else "10-K",
                    filing_date=date(2026, 2 + index, 1),
                    report_date=date(2025, 12, 31),
                    source_url=f"https://www.sec.gov/{index}",
                    raw_document=b"raw",
                    content_hash="hash",
                    retrieved_at=now,
                    attempted_at=now,
                    original_accession="0000000001-26-000001" if index == 2 else None,
                )
            )
            excerpt = f"We reported workforce restructuring in 2023. Example {index}."
            session.add(
                SecDocument(
                    accession=accession,
                    normalized_text=excerpt,
                    parser_version="test-1",
                    extractor_version="test-1",
                    raw_content_hash="hash",
                    parse_status="ok",
                    section_status="unknown",
                )
            )
            session.add(
                SecSignal(
                    accession=accession,
                    paragraph_index=0,
                    category="workforce_restructuring",
                    extractor_version="test-1",
                    rule_id="rule-1",
                    assertion="hypothetical_risk" if index == 2 else "reported_event",
                    start_offset=0,
                    end_offset=len(excerpt),
                    excerpt=excerpt,
                )
            )
            session.add(
                AtsJob(
                    id=f"job-{index}",
                    source="ats",
                    provider="greenhouse",
                    company_token="fixture",
                    company_name="Fixture Co",
                    external_id=str(index),
                    title="Engineer 100%" if index == 1 else "Engineer",
                    content_text="Full description excluded",
                    is_open=index != 2,
                    first_seen=now,
                    last_seen=now,
                )
            )
        session.commit()
    yield factory
    engine.dispose()


def test_filtered_pages_and_provenance(database: sessionmaker[Session]) -> None:
    with database() as session:
        first = read_page(
            session, SignalArgs(cik="1", assertion="reported_event", limit=1)
        )
        second = read_page(
            session, SignalArgs(cik="1", assertion="reported_event", limit=1, offset=1)
        )
        assert first.next_offset == 1 and first.has_more
        assert second.next_offset is None and not second.has_more
        assert first.items[0]["accession"] != second.items[0]["accession"]
        assert first.items[0]["event_date"] is None
        assert "2023" in str(first.items[0]["excerpt"])
        assert first.items[0]["filing_date"] == "2026-02-01"
        assert first.items[0]["end_offset"] == len(str(first.items[0]["excerpt"]))
        assert first.items[0]["provider"] == "greenhouse"
        assert not read_page(session, SignalArgs(category="missing")).items
        amendments = read_page(
            session, FilingArgs(form="10-K/A", since=date(2026, 4, 1))
        )
        assert amendments.returned == 1
        assert amendments.items[0]["original_accession"] == "0000000001-26-000001"
        assert "raw_document" not in amendments.items[0]
        assert "normalized_text" not in amendments.items[0]
        assert read_page(session, JobArgs()).returned == 2
        assert read_page(session, JobArgs(is_open=None)).returned == 3
        assert read_page(session, JobArgs(title_contains="%")).returned == 1
        assert not read_page(session, JobArgs(provider="lever")).items


def test_mcp_catalog_calls_validation_and_no_writes(
    database: sessionmaker[Session],
) -> None:
    statements: list[str] = []
    bind = database.kw["bind"]

    @event.listens_for(bind, "before_cursor_execute")
    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    async def run() -> None:
        async with Client(build_server(database)) as client:
            catalog = await client.list_tools()
            assert {tool.name for tool in catalog.tools} == {
                "list_sec_filings",
                "list_sec_signals",
                "list_jobs",
            }
            assert all(
                tool.annotations and tool.annotations.read_only_hint
                for tool in catalog.tools
            )
            for name in ("list_sec_filings", "list_sec_signals", "list_jobs"):
                result = await client.call_tool(name, {"limit": 1})
                assert isinstance(result, types.CallToolResult) and not result.is_error
                assert (
                    result.structured_content
                    and result.structured_content["returned"] == 1
                )
                assert isinstance(result.content[0], types.TextContent)
                assert json.loads(result.content[0].text) == result.structured_content
            for args in (
                {"limit": 0},
                {"limit": 201},
                {"limit": True},
                {"offset": -1},
                {"sql": "DELETE FROM jobs"},
                {"cik": "x"},
                {"since": "2026-04-01", "until": "2026-01-01"},
            ):
                result = await client.call_tool("list_sec_signals", args)
                assert isinstance(result, types.CallToolResult) and result.is_error
            result = await client.call_tool("delete_jobs", {})
            assert isinstance(result, types.CallToolResult) and result.is_error

    asyncio.run(run())
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
    assert all(
        "raw_document" not in statement and "normalized_text" not in statement
        for statement in statements
    )


def test_byte_limit_and_long_excerpt_are_explicit(
    database: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        async with Client(build_server(database)) as client:
            result = await client.call_tool("list_sec_signals", {})
            assert isinstance(result, types.CallToolResult) and result.is_error
            assert result.structured_content is None

    monkeypatch.setattr(server_module, "MAX_RESPONSE_BYTES", 100)
    asyncio.run(run())
    with database() as session:
        signal = session.get(
            SecSignal, ("0000000001-26-000000", 0, "workforce_restructuring")
        )
        assert signal is not None
        signal.excerpt = "x" * 131_073
        session.commit()
        with pytest.raises(ValueError, match="No partial excerpt"):
            read_page(session, SignalArgs())


def test_stdio_launch_and_database_failure(tmp_path: Path) -> None:
    async def run() -> None:
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'empty.db'}"}
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp.server"], env=env
        )
        async with Client(params) as client:
            catalog = await client.list_tools()
            assert len(catalog.tools) == 3
            result = await client.call_tool("list_sec_filings", {})
            assert isinstance(result, types.CallToolResult) and result.is_error
            assert isinstance(result.content[0], types.TextContent)
            assert "Database query unavailable" in result.content[0].text
            assert str(tmp_path) not in result.content[0].text

    asyncio.run(run())
