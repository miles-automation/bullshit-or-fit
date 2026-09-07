import hashlib
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, defer

from app.config import settings
from app.db import SessionLocal
from app.jobtrends.sec.client import SecClient
from app.jobtrends.sec.discovery import FilingRef, discover
from app.jobtrends.sec.models import SecDocument, SecFiling, SecSignal
from app.jobtrends.sec.registry import Issuer, load_registry
from app.jobtrends.sec.text import (
    PARSER_VERSION,
    Rules,
    extract,
    load_rules,
    normalize,
    paragraphs,
)

logger = logging.getLogger(__name__)


def rebuild_filing(session: Session, filing: SecFiling, rules: Rules) -> int:
    if filing.raw_document is None or filing.content_hash is None:
        raise ValueError("cannot rebuild a filing without stored raw material")
    session.execute(delete(SecSignal).where(SecSignal.accession == filing.accession))
    document = session.get(SecDocument, filing.accession)
    if document is None:
        document = SecDocument(accession=filing.accession)
        session.add(document)
    document.parser_version = PARSER_VERSION
    document.extractor_version = rules.fingerprint
    document.raw_content_hash = filing.content_hash
    document.normalized_text = ""
    document.section_status = "unknown"
    document.parse_status = "failed"
    document.parse_error = None
    try:
        document.normalized_text = normalize(filing.raw_document)
    except ValueError as exc:
        document.parse_error = str(exc)
        return 0
    document.parse_status = "ok"
    document.section_status = (
        "best_effort"
        if any(p.section for p in paragraphs(document.normalized_text))
        else "unknown"
    )
    signals = extract(document.normalized_text, rules)
    for signal in signals:
        session.add(
            SecSignal(
                accession=filing.accession,
                paragraph_index=signal.paragraph.index,
                category=signal.category,
                extractor_version=signal.extractor_version,
                rule_id=signal.rule_id,
                assertion=signal.assertion,
                section=signal.paragraph.section,
                start_offset=signal.paragraph.start,
                end_offset=signal.paragraph.end,
                excerpt=signal.paragraph.text,
            )
        )
    return len(signals)


def ingest(
    session: Session,
    client: SecClient,
    issuers: list[Issuer],
    rules: Rules,
    periods: int = 2,
    max_filings: int = 40,
) -> dict[str, Any]:
    if not 1 <= max_filings <= 200:
        raise ValueError("max_filings must be between 1 and 200")
    summary: dict[str, Any] = {
        "discovered": 0,
        "downloaded": 0,
        "download_attempts": 0,
        "cached": 0,
        "failed": 0,
        "signals_rebuilt": 0,
        "warnings": [],
        "filings": [],
    }
    candidates: list[tuple[Issuer, FilingRef, str | None]] = []
    for issuer in issuers:
        try:
            refs, warnings = discover(client, issuer, periods)
            summary["warnings"].extend(
                f"{issuer.cik}: {warning}" for warning in warnings
            )
        except Exception as exc:
            logger.exception("SEC discovery failed for CIK %s", issuer.cik)
            summary["failed"] += 1
            summary["warnings"].append(f"{issuer.cik}: discovery failed: {exc}")
            continue
        summary["discovered"] += len(refs)
        originals: dict[date, list[str]] = {}
        for ref in refs:
            if ref.form == "10-K":
                originals.setdefault(ref.report_date, []).append(ref.accession)
        for ref in refs:
            original = originals.get(ref.report_date, [])
            candidates.append(
                (
                    issuer,
                    ref,
                    original[0]
                    if ref.form == "10-K/A" and len(original) == 1
                    else None,
                )
            )
    stored_sizes: dict[str, int | None] = {
        accession: size
        for accession, size in session.execute(
            select(SecFiling.accession, func.length(SecFiling.raw_document)).where(
                SecFiling.accession.in_([ref.accession for _, ref, _ in candidates])
            )
        )
    }
    candidates.sort(
        key=lambda candidate: (
            0
            if stored_sizes.get(candidate[1].accession) is not None
            else 1
            if candidate[1].accession not in stored_sizes
            else 2
        )
    )
    deferred = 0
    for issuer, ref, original_accession in candidates:
        raw_size = stored_sizes.get(ref.accession)
        needs_download = raw_size is None
        if needs_download and summary["download_attempts"] >= max_filings:
            deferred += 1
            continue
        filing = session.scalar(
            select(SecFiling)
            .options(defer(SecFiling.raw_document))
            .where(SecFiling.accession == ref.accession)
        )
        if filing is None:
            filing = SecFiling(accession=ref.accession)
            session.add(filing)
        if needs_download:
            filing.cik = issuer.cik
            filing.issuer_name = issuer.name
            filing.provider = issuer.provider
            filing.company_token = issuer.company_token
            filing.form = ref.form
            filing.filing_date = ref.filing_date
            filing.report_date = ref.report_date
            filing.source_url = ref.url(issuer.cik)
            filing.attempted_at = datetime.now(UTC)
            summary["download_attempts"] += 1
            try:
                filing.raw_document = client.fetch(filing.source_url)
                raw_size = len(filing.raw_document)
                filing.content_hash = hashlib.sha256(filing.raw_document).hexdigest()
                filing.retrieved_at = datetime.now(UTC)
                filing.fetch_error = None
                summary["downloaded"] += 1
            except Exception as exc:
                filing.fetch_error = str(exc)
                summary["failed"] += 1
                logger.warning("SEC filing %s failed: %s", ref.accession, exc)
        else:
            summary["cached"] += 1
        filing.original_accession = original_accession
        session.flush()
        document = session.scalar(
            select(SecDocument)
            .options(defer(SecDocument.normalized_text))
            .where(SecDocument.accession == ref.accession)
        )
        if raw_size is not None and (
            document is None
            or document.parser_version != PARSER_VERSION
            or document.extractor_version != rules.fingerprint
        ):
            summary["signals_rebuilt"] += rebuild_filing(session, filing, rules)
            session.flush()
            document = session.get(SecDocument, ref.accession)
        if document is not None and document.parse_status != "ok":
            summary["failed"] += 1
        summary["filings"].append(
            {
                "cik": filing.cik,
                "accession": filing.accession,
                "form": filing.form,
                "filing_date": filing.filing_date.isoformat(),
                "report_date": filing.report_date.isoformat(),
                "source_url": filing.source_url,
                "bytes": raw_size or 0,
                "parse_status": document.parse_status if document else "not_parsed",
                "error": filing.fetch_error
                or (document.parse_error if document else None),
            }
        )
        session.commit()
    if deferred:
        summary["warnings"].append(
            f"max_filings download budget reached; {deferred} deferred filings; rerun to resume"
        )
    return summary


def rebuild(
    session: Session, rules: Rules, cik: str | None = None, limit: int = 200
) -> dict[str, int]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    query = (
        select(SecFiling.accession)
        .where(SecFiling.raw_document.is_not(None))
        .order_by(SecFiling.filing_date.desc(), SecFiling.accession)
    )
    if cik:
        query = query.where(SecFiling.cik == cik.zfill(10))
    accessions = list(session.scalars(query.limit(limit)))
    count = 0
    failed = 0
    processed = 0
    for accession in accessions:
        filing = session.get(SecFiling, accession)
        if filing is None:
            continue
        count += rebuild_filing(session, filing, rules)
        processed += 1
        session.flush()
        document = session.get(SecDocument, filing.accession)
        failed += int(document is not None and document.parse_status != "ok")
        session.commit()
    return {"filings": processed, "signals": count, "failed": failed}


def report(
    session: Session,
    cik: str | None = None,
    category: str | None = None,
    since: date | None = None,
    until: date | None = None,
    limit: int = 50,
    form: str | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    query = (
        select(SecSignal, SecFiling, SecDocument)
        .options(defer(SecFiling.raw_document), defer(SecDocument.normalized_text))
        .join(SecFiling, SecSignal.accession == SecFiling.accession)
        .join(SecDocument, SecDocument.accession == SecFiling.accession)
    )
    if cik:
        query = query.where(SecFiling.cik == cik.zfill(10))
    if category:
        query = query.where(SecSignal.category == category)
    if since:
        query = query.where(SecFiling.filing_date >= since)
    if until:
        query = query.where(SecFiling.filing_date <= until)
    if form:
        query = query.where(SecFiling.form == form)
    query = query.order_by(
        SecFiling.filing_date.desc(),
        SecFiling.accession,
        SecSignal.paragraph_index,
        SecSignal.category,
    ).limit(limit + 1)
    rows = list(session.execute(query))
    return {
        "source": "sec",
        "kind": "evidence_candidates",
        "truncated": len(rows) > limit,
        "date_basis": "filing_date; excerpt event dates may differ or be unknown",
        "signals": [
            {
                "cik": filing.cik,
                "issuer": filing.issuer_name,
                "provider": filing.provider,
                "company_token": filing.company_token,
                "accession": filing.accession,
                "form": filing.form,
                "original_accession": filing.original_accession,
                "filing_date": filing.filing_date.isoformat(),
                "report_date": filing.report_date.isoformat(),
                "source_url": filing.source_url,
                "content_hash": filing.content_hash,
                "retrieved_at": filing.retrieved_at.isoformat()
                if filing.retrieved_at
                else None,
                "parser_version": document.parser_version,
                "extractor_version": signal.extractor_version,
                "category": signal.category,
                "rule_id": signal.rule_id,
                "assertion": signal.assertion,
                "section": signal.section,
                "section_status": document.section_status,
                "paragraph_index": signal.paragraph_index,
                "start_offset": signal.start_offset,
                "end_offset": signal.end_offset,
                "excerpt": signal.excerpt,
                "event_date": None,
            }
            for signal, filing, document in rows[:limit]
        ],
    }


def refresh_if_enabled() -> None:
    if not settings.sec_enabled:
        return
    try:
        with SecClient(
            settings.sec_contact_email, Path(settings.sec_cache_dir)
        ) as client:
            with SessionLocal() as session:
                result = ingest(
                    session,
                    client,
                    load_registry(settings.sec_registry_path),
                    load_rules(settings.sec_rules_path),
                    settings.sec_periods,
                    settings.sec_max_filings,
                )
        logger.info(
            "SEC refresh: %s downloaded, %s cached, %s failed; warnings=%s",
            result["downloaded"],
            result["cached"],
            result["failed"],
            result["warnings"],
        )
    except Exception:
        logger.exception("SEC refresh failed; other sources remain independent")
