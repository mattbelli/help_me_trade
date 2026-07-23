// Strategy Lab — routing + actor-family model (pure logic, no React).
//
// The Lab is a built-in workspace view (the LAB_PAGE_ID sentinel), not a Next
// route, so its internal navigation lives in the URL *hash* — which makes actor
// pages deep-linkable from other surfaces, e.g.
//   /#lab/live/llm/acct_alpaca_fable
// while still rendering inside the workspace shell.

export type LabTab = "overview" | "live" | "backtesting";
export type ActorFamily = "mechanical" | "llm" | "human";

export type BookAccount = { id: string; label: string };

// ── v1 hardcoded account-id → family map ────────────────────────────────────
// There is no `family` column in the DB yet; these four account ids are the
// canonical LLM/human paper books (labels come from /api/strategies/accounts at
// runtime — kept here only as fallbacks / for the family lookup). When the DB
// grows a family field, delete this block and read it from the account row.
export const LLM_BOOK_ACCOUNTS: BookAccount[] = [
  { id: "acct_alpaca_deepseek", label: "Deep Seek" },
  { id: "acct_alpaca_fable", label: "Fable" },
  { id: "acct_alpaca_open", label: "Claude Panel" },
];
export const HUMAN_BOOK_ACCOUNTS: BookAccount[] = [
  { id: "acct_fc78907f4bda", label: "Matt + LLM" },
];

const BOOK_FAMILY = new Map<string, ActorFamily>([
  ...LLM_BOOK_ACCOUNTS.map((a) => [a.id, "llm"] as const),
  ...HUMAN_BOOK_ACCOUNTS.map((a) => [a.id, "human"] as const),
]);

export function familyForAccount(accountId: string): ActorFamily | null {
  return BOOK_FAMILY.get(accountId) ?? null;
}

/** Book-account ids that are surfaced as their own actor (llm + human). */
export const BOOK_ACCOUNT_IDS: string[] = [
  ...LLM_BOOK_ACCOUNTS,
  ...HUMAN_BOOK_ACCOUNTS,
].map((a) => a.id);

// ── Hash routing ────────────────────────────────────────────────────────────
export type LabRoute = {
  tab: LabTab;
  family: ActorFamily;
  actor: string | null;
};

export const DEFAULT_LAB_ROUTE: LabRoute = {
  tab: "overview",
  family: "mechanical",
  actor: null,
};

const TABS: LabTab[] = ["overview", "live", "backtesting"];
const FAMILIES: ActorFamily[] = ["mechanical", "llm", "human"];

/** True for any hash that should open the Lab (`#lab`, `#lab/...`). */
export function isLabHash(hash: string): boolean {
  return /^#lab(\/|$)/.test(hash);
}

export function parseLabHash(hash: string): LabRoute {
  const parts = hash.replace(/^#/, "").split("/").filter(Boolean);
  if (parts[0] !== "lab") return { ...DEFAULT_LAB_ROUTE };
  const tab = TABS.includes(parts[1] as LabTab)
    ? (parts[1] as LabTab)
    : "overview";
  if (tab !== "live") {
    return { tab, family: "mechanical", actor: null };
  }
  const family = FAMILIES.includes(parts[2] as ActorFamily)
    ? (parts[2] as ActorFamily)
    : "mechanical";
  const actor = parts[3] ? decodeURIComponent(parts[3]) : null;
  return { tab: "live", family, actor };
}

export function labHash(route: LabRoute): string {
  if (route.tab !== "live") return `#lab/${route.tab}`;
  if (route.actor) {
    return `#lab/live/${route.family}/${encodeURIComponent(route.actor)}`;
  }
  return `#lab/live/${route.family}`;
}

// ── ET "today" helpers (fills / job-runs are filtered to the ET session) ────
export function etDateKey(d: Date = new Date()): string {
  // en-CA yields YYYY-MM-DD; timeZone pins it to the exchange day.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export function isTodayET(iso: string | null | undefined): boolean {
  if (!iso) return false;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return false;
  return etDateKey(t) === etDateKey();
}

/** "9:41a" style ET clock for a timestamp. */
export function etClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
  })
    .format(t)
    .replace(" AM", "a")
    .replace(" PM", "p");
}
