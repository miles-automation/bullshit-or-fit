import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.jobtrends.sec.client import SecClient
from app.jobtrends.sec.registry import Issuer


class FilingRef(BaseModel):
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: str
    filing_date: date
    report_date: date
    document: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")

    def url(self, cik: str) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{self.accession.replace('-', '')}/{self.document}"


def filing_rows(data: dict[str, Any]) -> list[FilingRef]:
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    if any(not isinstance(data.get(key), list) for key in keys):
        raise ValueError("SEC submissions missing filing arrays")
    if len({len(data[key]) for key in keys}) != 1:
        raise ValueError("SEC submissions have mismatched filing arrays")
    return [
        FilingRef(
            accession=accession,
            form=form,
            filing_date=filing_date,
            report_date=report_date,
            document=document,
        )
        for accession, form, filing_date, report_date, document in zip(
            *(data[key] for key in keys), strict=True
        )
        if form in {"10-K", "10-K/A"}
    ]


def discover(
    client: SecClient, issuer: Issuer, periods: int
) -> tuple[list[FilingRef], list[str]]:
    if not 1 <= periods <= 5:
        raise ValueError("periods must be between 1 and 5")
    data = client.json(f"https://data.sec.gov/submissions/CIK{issuer.cik}.json")
    if str(data.get("cik", "")).zfill(10) != issuer.cik:
        raise ValueError("SEC submissions CIK does not match registry")
    filings = data["filings"]
    refs = filing_rows(filings["recent"])
    archives = sorted(
        filings.get("files", []), key=lambda row: row["filingTo"], reverse=True
    )
    warnings: list[str] = []
    for index, archive in enumerate(archives):
        report_periods = sorted(
            {row.report_date for row in refs if row.form == "10-K"}, reverse=True
        )
        if len(report_periods) >= periods:
            oldest_report = report_periods[periods - 1]
            if date.fromisoformat(archive["filingTo"]) < oldest_report:
                break
        if index >= 20:
            warnings.append(
                "historical submissions capped at 20 files; coverage may be incomplete"
            )
            break
        name = archive["name"]
        if not re.fullmatch(r"CIK[0-9]{10}-submissions-[0-9]+\.json", name):
            raise ValueError("invalid historical submissions filename")
        refs.extend(
            filing_rows(client.json(f"https://data.sec.gov/submissions/{name}"))
        )
    periods_found = sorted(
        {row.report_date for row in refs if row.form == "10-K"}, reverse=True
    )[:periods]
    if len(periods_found) < periods:
        warnings.append(
            f"only {len(periods_found)} original annual reporting periods found of {periods} requested"
        )
    selected = {row.accession: row for row in refs if row.report_date in periods_found}
    return sorted(
        selected.values(), key=lambda row: (row.filing_date, row.accession)
    ), warnings
