# Read-only MCP for BullshitOrFit

The local stdio server exposes stored SEC filing metadata, SEC evidence candidates, and ATS/board job metadata. It reads the existing database; it does not fetch filings, refresh jobs, run ingestion, or expose arbitrary SQL. Start it with a migrated database containing the records you want to analyze. An empty result means no matching stored records, not complete market or SEC coverage.

## Run and connect

From `backend`, run `uv sync --dev` and then `uv run bullshitorfit-mcp`. The equivalent module command is `uv run python -m app.mcp.server`. There is no listening network port or public `/mcp` route. Protocol messages use stdout; diagnostics use stderr.

Set `DATABASE_URL` in the launching environment or the backend's `.env`, following the existing database setup. A PostgreSQL role with SELECT on the four exposed tables and USAGE on `jobtrends` is recommended. Tool queries run in read-only transactions with a ten-second statement timeout on PostgreSQL. Database credentials and local OS process permissions are the access boundary; no separate MCP authentication is implemented. Every client that can launch this process can read the exposed records. Keep credentials out of committed config and transcripts.

For a client accepting standard MCP JSON configuration, replace the path with your checkout's backend directory. The database environment must be available to the child process.

```json
{
  "mcpServers": {
    "bullshitorfit": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/bullshit-or-fit/backend", "bullshitorfit-mcp"]
    }
  }
}
```

For Sluice, configure this as its one downstream server:

```toml
[servers.bullshitorfit]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/bullshit-or-fit/backend", "bullshitorfit-mcp"]
env = { DATABASE_URL = "${DATABASE_URL}" }
```

Launch Sluice with that config as documented in Sluice's README. BullshitOrFit requires Python 3.12+; Sluice's separate environment requires Python 3.14+. Neither process needs to share a Python environment. Sluice turns each page's `items` array into a table. Fetch every page before claiming complete counts; each page gets a separate handle and may require a SQL UNION. This interface does not change Sluice's behavior.

## Tools and schemas

`tools/list` supplies JSON input and output schemas and read-only annotations. This is an MCP contract, not an HTTP API, so no OpenAPI route is added. The server uses the [official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/).

All tools accept `limit` (integer 1–200, default 50) and `offset` (integer 0–1000000, default 0). Every response contains `items`, `returned`, `limit`, `offset`, `has_more`, `next_offset`, `coverage`, and `date_basis`. Structured content and the JSON text contain identical values. Unknown arguments, malformed dates, invalid enums and inverted date ranges are tool errors.

| Tool | Additional filters | Row content |
| --- | --- | --- |
| `list_sec_filings` | `cik` (1–10 digits), `accession`, `form` (`10-K` or `10-K/A`), `since`, `until` | Filing identity, issuer, provider/company token mapping, form, original accession for amendments, filing/report dates, official source URL, hash, retrieval/attempt timestamps, fetch-failure flag and available parse/version status |
| `list_sec_signals` | All filing filters plus `category` and `assertion` | Filing provenance plus exact excerpt, category, rule/extractor/parser versions, assertion, section/status, paragraph index and character offsets; `event_date` is always null |
| `list_jobs` | Exact `source`, `provider`, `company_token`; literal case-insensitive `title_contains`; `is_open` (default true, false for closed, null for either) | Stable ID, source/provider/company identity, external ID, title/location/department/URL, posting and observation timestamps, observed open status, compensation fields with currency/period/kind |

Categories follow the configured SEC extractor, so `category` is a string rather than a fixed enum. Assertion values are `reported_event`, `forward_looking_intention`, `hypothetical_risk`, `negated`, and `unknown`. Date filters are inclusive ISO dates (`YYYY-MM-DD`) over **filing date**, never inferred event date. A filing can describe an event years earlier. Candidates remain uncertain evidence, not company-health, hiring, or financial conclusions. See [SEC semantics](sec-filings.md).

Filings include failed retrievals and documents without successful parsing when present in storage. Signals include only extracted candidates. Filings with no candidates must not be inferred absent: query `list_sec_filings` separately. Raw documents and full normalized text are not exposed. Jobs include `ats_jobs` sources only, not HN posts; full job descriptions are excluded. `is_open` is the last ingested state, and `last_seen` must be considered before describing a role as current. SEC and job rows are separate datasets; join by both `provider` and `company_token`, never a fuzzy company-name match.

## Pagination and bounds

Repeat a call with unchanged filters and its `next_offset` until `has_more` is false. Filings sort by accession; signals by accession/paragraph/category; jobs by ID. These are deterministic orders over a fixed dataset, **not a snapshot across requests**. Concurrent ingestion or rebuilding can shift pages. Use a fixed database snapshot for reproducible comparisons and do not treat `returned` as a dataset total.

Each JSON response is capped at 400000 UTF-8 bytes before MCP framing. A response over that size returns a tool error with no partial page; retry the same offset with a smaller `limit`. A single oversized row cannot be fetched through this interface. Signal excerpts above 131072 characters produce an explicit error and must be retrieved with the existing SEC CLI; excerpts are never silently shortened. The SQL projection bounds excerpt retrieval and omits large raw documents and job bodies. The response cap is not a process memory guarantee. Database errors and timeouts return a generic tool error without SQL, credentials, or connection details. A failed query is never represented as an empty successful page.

## Example workflow

1. Call `list_sec_signals` with `{"category":"workforce_restructuring","assertion":"reported_event","limit":50}` and collect all pages.
2. Group/count distinct accession numbers, preserving the actual excerpts and reporting periods.
3. For each mapped employer, call `list_jobs` with its `provider` and `company_token`, collecting all pages.
4. Report stored observations and dates. Do not imply a causal link between a historical workforce event and current hiring.

Use this workflow directly and through Sluice against the same database snapshot for a comparison. No model benchmark, savings claim, or production activation is implied by installing the MCP server.
