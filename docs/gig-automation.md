# Gig-driven fulfillment automations

The automation backlog starts from concrete paid marketplace asks rather than
generic feature ideas. Each fixture in `backend/gig_specs/catalog.json` records
the original demand signal and turns its deliverable into a versioned executable
contract: input limits, fields, validation, provenance, acceptance, and output.

## Current vertical slice: PDF batch to checked workbook

The first runner is intentionally operator-facing. It does not accept public
uploads and does not silently guess. A run:

1. Rejects a batch outside its document/page/field limits.
2. Extracts configured fields from every text-bearing PDF; image-only scans are
   rejected for OCR or manual review.
3. Normalizes typed values such as dates and decimal quantities.
4. Flags missing, invalid, and ambiguous values for review.
5. Writes an XLSX delivery with source-page citations and an Exceptions sheet.
6. Writes a JSON audit manifest with source hashes, the spec fingerprint,
   acceptance result, and elapsed time.

Extracted values are escaped when necessary so untrusted document content cannot
be interpreted as an Excel formula. The unmodified value remains in the audit
manifest.

Run from the repository root:

```bash
make gig-pdf-run \
  SPEC=gig_specs/pdf/property-records.json \
  INPUT=/absolute/path/to/incoming \
  OUTPUT=/absolute/path/to/deliveries
```

The command exits `0` when the fixture acceptance threshold passes, `3` when it
produces a delivery requiring operator correction, and `2` when the batch cannot
be safely run. Correct exceptions in a copy of the delivery workbook; retain the
generated audit manifest unchanged.

## Adding a gig ask

- Record its public marketplace URL, observed date, title, and advertised budget.
- Add the narrowest honest batch constraints supported by the ask.
- Use one or more regex patterns per field, each with a named `value` group.
- Add the spec to the catalog and a fixture-driven test.
- Prefer a new fixture over weakening an existing fixture's limits or validation.

This is fulfillment tooling, not a public product boundary. Sensitive or
regulated documents remain out of scope until storage, retention, access control,
and deletion policies are implemented and reviewed.
