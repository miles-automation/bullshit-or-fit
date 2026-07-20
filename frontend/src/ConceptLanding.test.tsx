import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    // jsdom has no scrollIntoView; the CTA click scrolls to the reserve section
    Element.prototype.scrollIntoView = vi.fn();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the not-advice disclaimer in the footer (required for the financial concept)", async () => {
    render(<ConceptLanding slug="bookkeeping-invoice-automation" />);
    await waitFor(() =>
      expect(screen.getByText("Stop paying someone to reconcile your invoices")).toBeInTheDocument(),
    );
    expect(screen.getByText("Not career, financial, or legal advice.")).toBeInTheDocument();
  });

  it("shows the real price with the CTA, and page load logs only a view (clauses 1+2)", async () => {
    render(<ConceptLanding slug={CONCEPT.slug} />);
    await waitFor(() => expect(screen.getByText("$29/mo")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Get early access" })).toBeInTheDocument();
    const events = vi.mocked(api.logExpEvent).mock.calls.map(([, p]) => p.event_type);
    expect(events).toEqual(["view"]); // no intent without an explicit click
  });

  it("logs intent ONLY on the priced CTA click, then reveals the not-available note (clauses 2+5)", async () => {
    render(<ConceptLanding slug={CONCEPT.slug} />);
    await waitFor(() => expect(screen.getByText("$29/mo")).toBeInTheDocument());
    expect(screen.queryByRole("status")).not.toBeInTheDocument(); // note is click-gated
    fireEvent.click(screen.getByRole("button", { name: "Get early access" }));
    const intents = vi
      .mocked(api.logExpEvent)
      .mock.calls.filter(([, p]) => p.event_type === "intent");
    expect(intents).toHaveLength(1);
    expect(intents[0][1].tier).toBe("Solo");
    expect(screen.getByRole("status").textContent).toContain("isn't available yet");
    expect(screen.getByRole("status").textContent).toContain("not been charged");
  });
});
