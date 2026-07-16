# DECISION MEMO — Approve the TSLA/PLTR/GME ledger quantity offsets?
*Fable 5 · founder decision memo · 2026-07-06 · one page, one recommendation.*

## The question
All 4 real Schwab accounts are broker-confirmed FLAT, but the ledger still shows residual
quantities from shares acquired before the export window: TSLA −6 and PLTR −630 (Individual),
PLTR −900 (Roth Joint), GME −32. A `$0`-amount `manual_adjustment` insert that zeroes these is
written and dry-run tested — it has been waiting on your approval since 2026-07-02.

## What the adjustment does and does not touch
- **Does:** add one $0-amount event per (account, symbol) so quantity reconciles to the broker's
  flat reality. Closed-symbol recognition then reads true — those symbols stop polluting
  day/week realized views (this is the same class of error that once dumped TSLA's lifetime
  +$44.5k into a WEEK view).
- **Does not:** touch cash. Net-cash realized P&L — the source of truth — is computed from
  signed amounts and is unaffected by a $0 event. Fully reversible (delete the tagged events).

## The one real risk, and why it's acceptable
If any residual were actually caused by *missing events inside the window* (the known gaps:
options expirations #16, multi-leg/assignment edge cases), zeroing it would paper over a sync
bug. Two reasons this is acceptable: (1) the residuals match the pre-window story — these are
your long-history names, and the broker-flat check pins the *current* truth regardless of how
the ledger got there; (2) the protection against future drift isn't this adjustment either way —
it's the nightly ledger-vs-broker quantity reconciliation job (#17) that pages on NEW drift.

## Why not wait for statement-import?
The monthly-PDF importer gives *ongoing month-end anchors* — it validates balances going
forward. It does not retroactively produce the missing pre-window buys, so the per-symbol
quantity reads stay wrong however long you wait. The two are complementary: approve the offsets
to fix today's reads; statement-import keeps them honest monthly.

## Recommendation
**Approve, with two riders:**
1. Land **#17 (nightly qty reconciliation)** in the same round, so this is the *last* manual
   offset — any future drift pages instead of accumulating silently.
2. Keep the adjustment events tagged `manual_adjustment` and excluded from FIFO/round-trip
   trade stats (they're bookkeeping, not trades; net-cash is untouched by construction).

One sanity check before you say yes: eyeball that PLTR −630 / −900 are plausible pre-export
holdings in those two accounts (they're the largest offsets; TSLA −6 and GME −32 are trivially
plausible). If either PLTR number surprises you, pause and we audit that account's window first.
