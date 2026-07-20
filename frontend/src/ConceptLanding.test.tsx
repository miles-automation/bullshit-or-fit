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
  version: 1,
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
    // the impression echo: the label carries what THIS page rendered
    expect(intents[0][1].price_shown).toBe("$29/mo");
    expect(intents[0][1].concept_version).toBe(1);
    expect(screen.getByRole("status").textContent).toContain("isn't available yet");
    expect(screen.getByRole("status").textContent).toContain("not been charged");
  });

  it("logs reserve with the reserved tier's displayed price and version", async () => {
    render(<ConceptLanding slug={CONCEPT.slug} />);
    await waitFor(() => expect(screen.getByText("$29/mo")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Get early access" }));
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "a@b.co" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reserve my spot" }));
    const reserves = vi
      .mocked(api.logExpEvent)
      .mock.calls.filter(([, p]) => p.event_type === "reserve");
    expect(reserves).toHaveLength(1);
    expect(reserves[0][1].tier).toBe("Solo");
    expect(reserves[0][1].price_shown).toBe("$29/mo");
    expect(reserves[0][1].concept_version).toBe(1);
  });

  // THE CHROME PIN. EVERYTHING a visitor can read on this page — shared chrome
  // AND the (fixed) concept fixture — normalized and pinned as one string, so no
  // visible copy edit can slip past unpinned. If this test fails you changed
  // visitor-visible copy: bump PAGE_CHROME_VERSION in backend/app/experiments.py
  // (which invalidates every concept fingerprint there, forcing a version bump +
  // re-pin per concept) and THEN update the pin here. Do NOT just update the pin.
  it("pins ALL visible page copy (bump PAGE_CHROME_VERSION on change)", async () => {
    const norm = (s: string | null) => (s ?? "").replace(/\s+/g, " ").trim();
    const { container } = render(<ConceptLanding slug={CONCEPT.slug} />);
    await waitFor(() => expect(screen.getByText("$29/mo")).toBeInTheDocument());
    expect(norm(container.textContent)).toBe(PAGE_TEXT_PIN);
    fireEvent.click(screen.getByRole("button", { name: "Get early access" }));
    expect(norm(screen.getByRole("status").textContent)).toBe(
      "Solo isn't available yet — we're gauging interest before we build it. You have not been charged.",
    );
  });
});

const PAGE_TEXT_PIN =
  "For small-business ownersStop paying someone to reconcile your invoicesForward your invoices; get reconciled books back.See pricingReads every invoiceMatches to your ledgerFlags mismatchesHow it works1Forward invoices2We reconcile3Review + exportEarly-access pricingWe're gauging interest before we build this — reserve at this price and you won't be charged now.Solo$29/mocoreGet early accessReserve your spotWe're gauging interest before building this. Leave your email to reserve at this price — no charge now, and we'll only reach out if it's happening.EmailReserve my spotPrivacyTermsEarly access — gauging interest. No charge.Not career, financial, or legal advice.";
