import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.automations.spec import PdfGigSpec, load_pdf_spec

SPECS = Path(__file__).parents[1] / "gig_specs"


def test_catalog_specs_are_executable_and_fingerprinted() -> None:
    catalog = json.loads((SPECS / "catalog.json").read_text())
    fingerprints = set()
    for fixture in catalog["fixtures"]:
        spec = load_pdf_spec(SPECS / fixture["spec"])
        assert spec.slug == fixture["slug"]
        assert len(spec.fields) <= spec.limits.max_fields
        assert len(spec.fingerprint) == 16
        fingerprints.add(spec.fingerprint)
    assert len(fingerprints) == len(catalog["fixtures"])


def test_pattern_requires_named_value_group() -> None:
    payload = load_pdf_spec(SPECS / "pdf/property-records.json").model_dump()
    payload["fields"][0]["patterns"] = [r"Parcel: ([A-Z0-9-]+)"]
    with pytest.raises(ValidationError, match="named 'value' group"):
        PdfGigSpec.model_validate(payload)


def test_field_count_cannot_exceed_declared_limit() -> None:
    payload = load_pdf_spec(SPECS / "pdf/property-records.json").model_dump()
    payload["limits"]["max_fields"] = 1
    with pytest.raises(ValidationError, match="field count exceeds"):
        PdfGigSpec.model_validate(payload)
