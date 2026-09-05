"""Versioned, executable specifications for bounded fulfillment gigs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GigSource(BaseModel):
    """The paid-demand observation that motivated an automation fixture."""

    marketplace: str
    url: str
    observed_at: date
    title: str
    advertised_budget_usd: int | None = Field(default=None, ge=0)


class BatchLimits(BaseModel):
    max_documents: int = Field(gt=0, le=10_000)
    max_total_pages: int = Field(gt=0, le=100_000)
    max_pages_per_document: int = Field(gt=0, le=10_000)
    max_fields: int = Field(gt=0, le=100)
    min_extracted_text_characters: int = Field(default=20, ge=0, le=100_000)


class AcceptanceSpec(BaseModel):
    """Machine-checkable floor; exceptions still remain visible to the operator."""

    max_required_exception_rate: float = Field(default=0.01, ge=0.0, le=1.0)


class OutputSpec(BaseModel):
    workbook_sheet: str = Field(
        default="Data", min_length=1, max_length=31, pattern=r"^[^\\/*?:\[\]]+$"
    )
    include_source_page: bool = True
    include_field_status: bool = True


class ExtractionField(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    patterns: list[str] = Field(min_length=1)
    required: bool = True
    value_type: Literal["text", "integer", "decimal", "date", "email"] = "text"
    validation_pattern: str | None = None
    date_formats: list[str] = Field(
        default_factory=lambda: ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"]
    )

    @model_validator(mode="after")
    def validate_patterns(self) -> ExtractionField:
        for pattern in self.patterns:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid pattern for {self.name}: {exc}") from exc
            if "value" not in compiled.groupindex:
                raise ValueError(
                    f"pattern for {self.name} must contain a named 'value' group"
                )
        if self.validation_pattern:
            try:
                re.compile(self.validation_pattern)
            except re.error as exc:
                raise ValueError(
                    f"invalid validation pattern for {self.name}: {exc}"
                ) from exc
        return self


class PdfGigSpec(BaseModel):
    schema_version: Literal[1] = 1
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str
    kind: Literal["pdf_to_table"] = "pdf_to_table"
    source: GigSource
    limits: BatchLimits
    acceptance: AcceptanceSpec = Field(default_factory=AcceptanceSpec)
    output: OutputSpec = Field(default_factory=OutputSpec)
    fields: list[ExtractionField] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_field_contract(self) -> PdfGigSpec:
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        if len(self.fields) > self.limits.max_fields:
            raise ValueError("field count exceeds limits.max_fields")
        return self

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_pdf_spec(path: str | Path) -> PdfGigSpec:
    return PdfGigSpec.model_validate_json(Path(path).read_text())
