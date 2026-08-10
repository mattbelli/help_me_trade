/** One seam for backend-enum → human words.
 *
 *  Widgets render backend vocabulary straight from the wire (Alpaca's "new",
 *  the ledger's "data_gap"), so each one grew its own `.replace(/_/g," ")`.
 *  This is the one place that translation lives.
 *
 *  House style: `label()` always returns LOWERCASE. A column that wants caps
 *  applies CSS `uppercase` itself (that is a column style, not vocabulary).
 *
 *  The maps below are OVERRIDES ONLY — a value is listed when the literal
 *  reads wrong or is jargon. Everything else (and every value we have never
 *  seen) falls through to lowercase + underscores→spaces, so an unknown enum
 *  can never crash a widget and can never leak an underscore to the user.
 *
 *  Pure module: no React, no imports. Usable from anywhere, including tests.
 */

export type LabelKind =
  | "order_status"
  | "order_type"
  | "execution_mode"
  | "idea_status"
  | "idea_origin"
  | "user_action"
  | "journal_outcome"
  | "journal_decision"
  | "trade_side"
  | "report_kind";

const LABELS: Record<LabelKind, Record<string, string>> = {
  // Two sources land here: the snapshot's open orders (raw broker status,
  // Alpaca's lifecycle) and db/order_executions (our own pending/submitted/
  // dry_run/error/rejected plus the reconciled broker terminals).
  order_status: {
    new: "working", // Alpaca's live-at-the-exchange state — not "recently created"
    pending_new: "pending",
    partially_filled: "partial fill",
    pending_cancel: "canceling",
    pending_replace: "replacing",
    pending_review: "in review",
    calculated: "settling", // filled/done for the day, settlement still computing
    cancelled: "canceled", // one spelling only — never two states in one column
  },

  // market / limit / stop / stop_limit / trailing_stop (+ Schwab's lowercased
  // market_on_close, net_debit, …). Every one of them reads correctly through
  // the fallback, so there is nothing to override — the kind exists so callers
  // name the vocabulary they are rendering.
  order_type: {},

  // config/settings.py: dry_run | paper | live. All three are already the words
  // the founder sets in settings; renaming them here would split the vocabulary.
  execution_mode: {},

  // db/idea_outcomes: pending | triggered | won | lost | expired | invalidated |
  // data_gap. The fallback is the whole fix here ("data_gap" → "data gap").
  idea_status: {},

  // Open-ended by construction: `strategy:<id>` and `scan:<rule_id>` are built at
  // runtime, so only the fixed lanes are mapped. The book lanes are the ones the
  // fallback cannot help with — `:` and `/` are machine syntax, not words.
  idea_origin: {
    fusion_mech: "fusion (mechanical)",
    structure_vetoed: "vetoed structure",
    "book:deepseek": "deep seek book",
    "book:deepseek/considered": "deep seek book · considered",
    "book:fable": "fable book",
    "book:fable/considered": "fable book · considered",
    "book:panel": "claude panel book",
    "book:panel/bull": "claude panel · bull seat",
    "book:panel/bear": "claude panel · bear seat",
    "book:panel/macro": "claude panel · macro seat",
    "book:panel/risk": "claude panel · risk seat",
  },

  // db/idea_outcomes.USER_ACTIONS: taken | skipped. Both are plain words already.
  user_action: {},

  // decision_journal.outcome: win | loss | scratch | open | never_filled |
  // closed_incomplete (+ "unresolved" synthesized in the stats block), and the
  // my-trades round-trips reuse win/loss/scratch.
  journal_outcome: {
    scratch: "breakeven", // trader jargon for a flat trade (P&L exactly 0)
    closed_incomplete: "closed (no exit price)", // exit fill missing — P&L unknown, never faked
  },

  // decision_journal.decision: approved | rejected | expired (proposals) and the
  // regime-sleeve synthetic events.
  journal_decision: {
    kill_check_pass: "kill check passed",
    kill_check_fail: "kill check failed",
  },

  // The journal's "me" round-trips report long | option here (option trades carry
  // their asset class in this field — backend shape, not something to rename).
  trade_side: {},

  // Mirrors db/reports.REPORT_KIND_LABELS, lowercased for house style. Keep the
  // two in step: the phone reads the backend's `kind_label`, the cockpit reads this.
  report_kind: {
    on_request: "on-request report",
    flow_decomp: "flow decomposition",
    book_deepseek: "deep seek book",
    book_fable: "fable book",
    book_panel: "claude panel book",
    book_matt: "matt + llm book",
  },
};

/** Human words for a backend enum value. Always lowercase; never underscores. */
export function label(kind: LabelKind, value: string | null | undefined): string {
  const key = (value ?? "").trim().toLowerCase();
  return LABELS[kind][key] ?? key.replace(/_/g, " ");
}
