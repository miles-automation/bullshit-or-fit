import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConceptLanding } from "./ConceptLanding";
import * as api from "./api";

const CONCEPT: api.ExpConcept = {
  slug: "bookkeeping-invoice-automation",
  name: "Invoices -> reconciled books",
  badge: "For small-business owners",
  headline: "Stop paying someone to reconcile your invoices",
  subhead: "Forward your invoices; get reconciled books back.",
  bullets: ["Reads every invoice", "Matches to your ledger", "Flags mismatches"],
  how_it_works: ["Forward invoices", "We reconcile", "Review + export"],
  tiers: [
    { name: "Solo", price: "$29/mo", blurb: "core", cta_label: "Get early access", checkout_url: null },
  ],
  accent: null,
};

describe("ConceptLanding", () => {
  beforeEach(() => {
    vi.spyOn(api, "fetchConcept").mockResolvedValue(CONCEPT);
    vi.spyOn(api, "logExpEvent").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the not-advice disclaimer in the footer (required for the financial concept)", async () => {
    render(<ConceptLanding slug="bookkeeping-invoice-automation" />);
    await waitFor(() =>
      expect(screen.getByText("Stop paying someone to reconcile your invoices")).toBeInTheDocument(),
    );
    expect(screen.getByText("Not career, financial, or legal advice.")).toBeInTheDocument();
  });
});
