# Mobile agent brief — backend changes 2026-07-23 (LLM books + report markdown)

*For the mobile-app agent on the Mac. Written by the backend orchestrator; the founder will hand
this over. Everything here is live on serv as of 07-23 evening.*

## 1. The one fix the founder needs from you: render report bodies as markdown

Reports now carry a first-class `format` field: `"text" | "html" | "markdown"`.

- `GET /api/reports/{id}` and the report list both return it.
- **When `format === "markdown"`, render `body` through a markdown component** (GFM: headings,
  tables, bold, lists). Today the mobile app shows these as raw text — that's the bug the founder
  sees every night now.
- `format === "html"` (briefs, flow_decomp) and `"text"` behave as before.
- The web reference implementation renders these through the same component as deep-research
  reports (react-markdown + remark-gfm).

## 2. Why this matters nightly now: the LLM paper-books program

Four paper books each file a nightly analyst report **after hours** (~17:05–18:00 ET), all
markdown, all delivered to the founder:

| Book | report `kind` | What it is |
|------|--------------|------------|
| Deep Seek | `book_deepseek` | DeepSeek-driven analyst; registers candidates in idea-ledger lanes |
| Fable | `book_fable` | Claude session analyst (solo) |
| Claude Panel | `book_panel` | 4 Opus seats (bull/bear/macro/risk) + judge; per-seat lanes |
| Matt + LLM | `book_matt` | Advisory-only report on the founder's co-managed paper account |

Delivery per report:
- an **inbox notification** — `kind: "report_ready"`, `action_url: "/reports/<id>"`,
  `data: {report_id, kind, book}`;
- a **phone push** (APNs) with `data: {type: "report_ready", report_id, kind}` — deep-linking a
  tap to the report view is the ideal mobile behavior.
- a `book_deadman` warning notification fires at 20:00 ET if any book failed to file.

## 3. Other backend changes today (context, no mobile action needed)

- PR #102 chart perf: incremental bars refresh + broadened prewarm + web sessionStorage cache —
  `/api/bars` contract unchanged.
- Strategy records `book_deepseek` / `book_fable` / `book_panel` exist (disabled + propose) — they
  appear in `/api/strategies`; the web Lab filters them out of the mechanical roster.
- New idea-ledger origins: `book:deepseek|fable|panel` (+ journal-class `…/considered` and
  `book:panel/<seat>`) — visible via `/api/ideas/ledger?origin=`.
- Proposals from books only exist once the founder ARMS a book; the existing biometric approvals
  flow is unchanged and will carry them when that happens.

Spec, if needed: `References/LLM_BOOKS_PROGRAM_SPEC_2026-07-23.md`.

## 4. Addendum (07-23 late) — books ARMED; approval card needs account attribution

The books went LIVE tonight (all three armed, propose mode). Two things now concern mobile:

1. **Approval card must show WHICH account/book is trading** (founder caught it missing on the
   first live proposal). The backend now provides, everywhere a proposal travels:
   - APNs proposal push payload: new top-level keys `account_id` and `account_label`
     (e.g. `"acct_alpaca_fable"` / `"Fable (LLM book)"`); the alert body is also label-prefixed
     ("Fable (LLM book) · BUY 12 MU @ market").
   - `GET /api/ai/proposals/pending` and `GET /api/ai/proposals/{id}`: each row now carries
     `account_label` (nullable, fail-soft) alongside the existing `account_id`.
   Render the label prominently on the approval card (and anywhere proposals list).
2. **Book proposals are overnight objects now**: created after hours, `expires_at` = next session
   close (not 30 min). Approval before ~9:28 ET fills at the opening auction; the card's expiry
   countdown should handle a ~20h TTL gracefully.
