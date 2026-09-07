import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"


def upgrade() -> None:
    op.create_table(
        "sec_filings",
        sa.Column("accession", sa.Text(), primary_key=True),
        sa.Column("cik", sa.Text(), nullable=False),
        sa.Column("issuer_name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text()),
        sa.Column("company_token", sa.Text()),
        sa.Column("form", sa.Text(), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("original_accession", sa.Text()),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("raw_document", sa.LargeBinary()),
        sa.Column("content_hash", sa.Text()),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetch_error", sa.Text()),
        schema="jobtrends",
    )
    op.create_index(
        "ix_jobtrends_sec_filings_cik", "sec_filings", ["cik"], schema="jobtrends"
    )
    op.create_index(
        "ix_jobtrends_sec_filings_filing_date",
        "sec_filings",
        ["filing_date"],
        schema="jobtrends",
    )
    op.create_table(
        "sec_documents",
        sa.Column(
            "accession",
            sa.Text(),
            sa.ForeignKey("jobtrends.sec_filings.accession"),
            primary_key=True,
        ),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("raw_content_hash", sa.Text(), nullable=False),
        sa.Column("parse_status", sa.Text(), nullable=False),
        sa.Column("parse_error", sa.Text()),
        sa.Column("section_status", sa.Text(), nullable=False),
        schema="jobtrends",
    )
    op.create_table(
        "sec_signals",
        sa.Column(
            "accession",
            sa.Text(),
            sa.ForeignKey("jobtrends.sec_filings.accession"),
            primary_key=True,
        ),
        sa.Column("paragraph_index", sa.Integer(), primary_key=True),
        sa.Column("category", sa.Text(), primary_key=True),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("assertion", sa.Text(), nullable=False),
        sa.Column("section", sa.Text()),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        schema="jobtrends",
    )


def downgrade() -> None:
    for table in ("sec_signals", "sec_documents", "sec_filings"):
        op.drop_table(table, schema="jobtrends")
