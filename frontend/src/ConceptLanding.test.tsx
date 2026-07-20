import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
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

  // THE CHROME PIN. EVERYTHING a visitor can read across the page's three
  // states (initial, post-CTA note, post-reservation) — shared chrome AND the
  // fixed concept fixture — normalized and pinned, so no visible copy edit can
  // slip past unpinned. The sha256 of the combined text is cross-checked
  // against backend/app/page_chrome.json, which the backend folds into every
  // concept fingerprint: updating the JSON (the ONLY way to fix a copy change)
  // mechanically breaks every backend version pin, forcing a version bump +
  // re-pin per concept. The string pins below exist for a readable diff.
  it("pins ALL visible page copy and its hash (backend/app/page_chrome.json)", async () => {
    vi.spyOn(api, "submitLead").mockImplementation(
      (() => Promise.resolve({})) as unknown as typeof api.submitLead,
    );
    const norm = (s: string | null) => (s ?? "").replace(/\s+/g, " ").trim();
    const { container } = render(<ConceptLanding slug={CONCEPT.slug} />);
    await waitFor(() => expect(screen.getByText("$29/mo")).toBeInTheDocument());
    const initial = norm(container.textContent);
    fireEvent.click(screen.getByRole("button", { name: "Get early access" }));
    const note = norm(screen.getByRole("status").textContent);
    fireEvent.change(screen.getByLabelText("Email"), {
      target: { value: "a@b.co" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reserve my spot" }));
    await waitFor(() =>
      expect(screen.getByText("You're on the list")).toBeInTheDocument(),
    );
    const reserved = norm(container.textContent);

    expect(initial).toBe(PAGE_TEXT_PIN);
    expect(note).toBe(NOTE_PIN);
    expect(reserved).toBe(RESERVED_TEXT_PIN);

    const chromeJson = JSON.parse(
      readFileSync(
        resolve(dirname(fileURLToPath(import.meta.url)), "../../backend/app/page_chrome.json"),
        "utf-8",
      ),
    ) as { text_sha256: string };
    const hash = createHash("sha256")
      .update([initial, note, reserved].join("\n"))
      .digest("hex");
    expect(
      hash,
      "visible copy changed: update backend/app/page_chrome.json to this hash — " +
        "that breaks every backend concept-version pin, which is the point: bump " +
        "each concept's version and re-pin",
    ).toBe(chromeJson.text_sha256);
  });
});

const PAGE_TEXT_PIN =
  "For small-business ownersStop paying someone to reconcile your invoicesForward your invoices; get reconciled books back.See pricingReads every invoiceMatches to your ledgerFlags mismatchesHow it works1Forward invoices2We reconcile3Review + exportEarly-access pricingWe're gauging interest before we build this — reserve at this price and you won't be charged now.Solo$29/mocoreGet early accessReserve your spotWe're gauging interest before building this. Leave your email to reserve at this price — no charge now, and we'll only reach out if it's happening.EmailReserve my spotPrivacyTermsEarly access — gauging interest. No charge.Not career, financial, or legal advice.";
const NOTE_PIN =
  "Solo isn't available yet — we're gauging interest before we build it. You have not been charged.";
const RESERVED_TEXT_PIN =
  "For small-business ownersStop paying someone to reconcile your invoicesForward your invoices; get reconciled books back.See pricingReads every invoiceMatches to your ledgerFlags mismatchesHow it works1Forward invoices2We reconcile3Review + exportEarly-access pricingWe're gauging interest before we build this — reserve at this price and you won't be charged now.Solo$29/mocoreGet early accessYou're on the listThanks — you won't be charged. We'll email you only if we build this.PrivacyTermsEarly access — gauging interest. No charge.Not career, financial, or legal advice.";
