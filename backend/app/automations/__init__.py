"""Operator-run fulfillment automations derived from concrete paid gig asks."""

from app.automations.pdf_batch import AutomationError, run_pdf_batch
from app.automations.spec import PdfGigSpec, load_pdf_spec

__all__ = ["AutomationError", "PdfGigSpec", "load_pdf_spec", "run_pdf_batch"]
