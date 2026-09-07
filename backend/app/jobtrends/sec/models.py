from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.jobtrends.models import SCHEMA


class SecFiling(Base):
    __tablename__ = "sec_filings"
    __table_args__ = {"schema": SCHEMA}

    accession: Mapped[str] = mapped_column(Text, primary_key=True)
    cik: Mapped[str] = mapped_column(Text, index=True)
    issuer_name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    company_token: Mapped[str | None] = mapped_column(Text)
    form: Mapped[str] = mapped_column(Text)
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    report_date: Mapped[date] = mapped_column(Date)
    original_accession: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    raw_document: Mapped[bytes | None] = mapped_column(LargeBinary)
    content_hash: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetch_error: Mapped[str | None] = mapped_column(Text)


class SecDocument(Base):
    __tablename__ = "sec_documents"
    __table_args__ = {"schema": SCHEMA}

    accession: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.sec_filings.accession"), primary_key=True
    )
    normalized_text: Mapped[str] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(Text)
    extractor_version: Mapped[str] = mapped_column(Text)
    raw_content_hash: Mapped[str] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(Text)
    parse_error: Mapped[str | None] = mapped_column(Text)
    section_status: Mapped[str] = mapped_column(Text)


class SecSignal(Base):
    __tablename__ = "sec_signals"
    __table_args__ = {"schema": SCHEMA}

    accession: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.sec_filings.accession"), primary_key=True
    )
    paragraph_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    extractor_version: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(Text)
    assertion: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(Text)
