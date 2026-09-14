from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.jobtrends.models import AtsJob
from app.jobtrends.sec.models import SecDocument, SecFiling, SecSignal

TextFilter = Annotated[str, Field(min_length=1, max_length=200)]


class PageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 50
    offset: Annotated[int, Field(strict=True, ge=0, le=1_000_000)] = 0


class FilingArgs(PageArgs):
    cik: Annotated[str, Field(pattern=r"^[0-9]{1,10}$")] | None = None
    accession: (
        Annotated[str, Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")] | None
    ) = None
    form: Literal["10-K", "10-K/A"] | None = None
    since: date | None = None
    until: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "FilingArgs":
        if self.since and self.until and self.since > self.until:
            raise ValueError("since must be on or before until")
        return self


class SignalArgs(FilingArgs):
    category: TextFilter | None = None
    assertion: (
        Literal[
            "reported_event",
            "forward_looking_intention",
            "hypothetical_risk",
            "negated",
            "unknown",
        ]
        | None
    ) = None


class JobArgs(PageArgs):
    source: TextFilter | None = None
    provider: TextFilter | None = None
    company_token: TextFilter | None = None
    title_contains: TextFilter | None = None
    is_open: Annotated[bool, Field(strict=True)] | None = True


class RowPage(BaseModel):
    items: list[dict[str, JsonValue]]
    returned: int
    has_more: bool
    next_offset: int | None
    limit: int
    offset: int
    coverage: str
    date_basis: str


FILING_COLUMNS = (
    SecFiling.accession,
    SecFiling.cik,
    SecFiling.issuer_name.label("issuer"),
    SecFiling.provider,
    SecFiling.company_token,
    SecFiling.form,
    SecFiling.original_accession,
    SecFiling.filing_date,
    SecFiling.report_date,
    SecFiling.source_url,
    SecFiling.content_hash,
    SecFiling.retrieved_at,
)


def filing_query(args: FilingArgs) -> Select[tuple[object, ...]]:
    query = select(
        *FILING_COLUMNS,
        SecFiling.attempted_at,
        SecFiling.fetch_error.is_not(None).label("fetch_failed"),
        SecDocument.parse_status,
        SecDocument.section_status,
        SecDocument.parser_version,
        SecDocument.extractor_version,
    ).outerjoin(SecDocument, SecDocument.accession == SecFiling.accession)
    return filter_filings(query, args).order_by(SecFiling.accession)


def filter_filings(
    query: Select[tuple[object, ...]], args: FilingArgs
) -> Select[tuple[object, ...]]:
    if args.cik is not None:
        query = query.where(SecFiling.cik == args.cik.zfill(10))
    if args.accession is not None:
        query = query.where(SecFiling.accession == args.accession)
    if args.form is not None:
        query = query.where(SecFiling.form == args.form)
    if args.since is not None:
        query = query.where(SecFiling.filing_date >= args.since)
    if args.until is not None:
        query = query.where(SecFiling.filing_date <= args.until)
    return query


def signal_query(args: SignalArgs) -> Select[tuple[object, ...]]:
    query = (
        select(
            *FILING_COLUMNS,
            SecDocument.parser_version,
            SecDocument.section_status,
            SecSignal.extractor_version,
            SecSignal.paragraph_index,
            SecSignal.category,
            SecSignal.assertion,
            SecSignal.rule_id,
            SecSignal.section,
            SecSignal.start_offset,
            SecSignal.end_offset,
            func.substr(SecSignal.excerpt, 1, 131_073).label("excerpt"),
        )
        .select_from(SecSignal)
        .join(SecFiling, SecSignal.accession == SecFiling.accession)
        .join(SecDocument, SecDocument.accession == SecFiling.accession)
    )
    query = filter_filings(query, args)
    if args.category is not None:
        query = query.where(SecSignal.category == args.category)
    if args.assertion is not None:
        query = query.where(SecSignal.assertion == args.assertion)
    return query.order_by(
        SecSignal.accession, SecSignal.paragraph_index, SecSignal.category
    )


def job_query(args: JobArgs) -> Select[tuple[object, ...]]:
    query = select(
        AtsJob.id,
        AtsJob.source,
        AtsJob.provider,
        AtsJob.company_token,
        AtsJob.company_name,
        AtsJob.external_id,
        AtsJob.title,
        AtsJob.location,
        AtsJob.department,
        AtsJob.url,
        AtsJob.posted_at,
        AtsJob.first_seen,
        AtsJob.last_seen,
        AtsJob.is_open,
        AtsJob.comp_min,
        AtsJob.comp_max,
        AtsJob.comp_currency,
        AtsJob.comp_period,
        AtsJob.comp_kind,
    )
    for column, value in (
        (AtsJob.source, args.source),
        (AtsJob.provider, args.provider),
        (AtsJob.company_token, args.company_token),
        (AtsJob.is_open, args.is_open),
    ):
        if value is not None:
            query = query.where(column == value)
    if args.title_contains is not None:
        query = query.where(
            AtsJob.title.icontains(args.title_contains, autoescape=True)
        )
    return query.order_by(AtsJob.id)


def read_page(session: Session, args: PageArgs) -> RowPage:
    if isinstance(args, SignalArgs):
        query = signal_query(args)
        coverage = "Stored SEC evidence candidates only; classifications are uncertain, not company-health conclusions."
        date_basis = "Filters use filing_date, not event date. Event dates are not inferred; read the exact excerpt."
    elif isinstance(args, FilingArgs):
        query = filing_query(args)
        coverage = "Stored SEC filings only; missing issuers/periods are not evidence of no filings. Amendments remain separate."
        date_basis = (
            "Filters use filing_date; report_date identifies the reporting period."
        )
    elif isinstance(args, JobArgs):
        query = job_query(args)
        coverage = "Stored ATS/board jobs only, excluding HN posts. is_open is the last observed state, not a live availability check."
        date_basis = "posted_at is source-provided; first_seen/last_seen are collection timestamps."
    else:
        raise ValueError("Unsupported query")
    rows = (
        session.execute(query.offset(args.offset).limit(args.limit + 1))
        .mappings()
        .all()
    )
    items: list[dict[str, JsonValue]] = []
    for row in rows[: args.limit]:
        item: dict[str, JsonValue] = {}
        for key, value in row.items():
            if isinstance(value, date | datetime):
                item[str(key)] = value.isoformat()
            elif value is None or isinstance(value, str | int | float | bool):
                item[str(key)] = value
            else:
                raise ValueError("Unsupported stored field type")
        if isinstance(args, SignalArgs):
            excerpt = item["excerpt"]
            if not isinstance(excerpt, str) or len(excerpt) > 131_072:
                raise ValueError(
                    "A signal excerpt exceeds the 131072-character limit; retrieve it using the SEC CLI. No partial excerpt was returned."
                )
            item["event_date"] = None
        items.append(item)
    has_more = len(rows) > args.limit
    if has_more and args.offset + len(items) > 1_000_000:
        raise ValueError("Pagination offset limit reached; narrow the filters.")
    return RowPage(
        items=items,
        returned=len(items),
        has_more=has_more,
        next_offset=args.offset + len(items) if has_more else None,
        limit=args.limit,
        offset=args.offset,
        coverage=coverage,
        date_basis=date_basis,
    )
