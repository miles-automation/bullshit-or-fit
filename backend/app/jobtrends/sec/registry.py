import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.jobtrends.ats import SEED_COMPANIES


class Issuer(BaseModel):
    name: str
    cik: str = Field(pattern=r"^[0-9]{10}$")
    provider: str | None = None
    company_token: str | None = None
    verification_url: str

    @model_validator(mode="after")
    def paired_mapping(self) -> "Issuer":
        if bool(self.provider) != bool(self.company_token):
            raise ValueError("provider and company_token must be supplied together")
        return self


def load_registry(path: str = "") -> list[Issuer]:
    source = Path(path) if path else Path(__file__).with_name("issuers.json")
    issuers = [Issuer.model_validate(row) for row in json.loads(source.read_text())]
    ciks = [issuer.cik for issuer in issuers]
    mappings = [
        (issuer.provider, issuer.company_token)
        for issuer in issuers
        if issuer.company_token
    ]
    if len(set(ciks)) != len(ciks) or len(set(mappings)) != len(mappings):
        raise ValueError("duplicate CIK or employer mapping in SEC registry")
    return issuers


def unmatched_employers(issuers: list[Issuer]) -> list[dict[str, str]]:
    mapped = {(issuer.provider, issuer.company_token) for issuer in issuers}
    return [
        {"name": company.name, "provider": company.provider, "token": company.token}
        for company in SEED_COMPANIES
        if (company.provider, company.token) not in mapped
    ]
