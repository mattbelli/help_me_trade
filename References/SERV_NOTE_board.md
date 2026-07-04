# serv → mac: new shared artifact — the founder's kanban (References/board.json)

*Founder ask (2026-07-04): he's getting lost in the big md files and wants an always-on
visual overview. Solution: a repo-tracked board file both agents maintain, rendered
read-only at `/board` in the web app (`GET /api/board`, bearer auth like everything).*

## The rule (now in CLAUDE.md cadence #3 — applies to you too)
Whenever work starts / finishes / appears, update `References/board.json`: move the card,
add a card, or retire it. Same discipline as BUILD/HANDOFF. A stale board is worse than no
board. Commit it with your round (it will conflict occasionally — it's JSON, rebase is easy;
keep cards one-per-line-ish as seeded).

## Schema (seeded file is the reference)
- `lanes`: `founder` ("Waiting on you" — decisions only he can make), `in_progress`,
  `next`, `backlog`, `done` (keep ~10 newest; older history lives in BUILD.md).
- Card: `{id, title, lane, type: feature|bug|safety|research|ops, priority: high|normal|low,
  notes (one line), owner: serv|mac|founder|serv+mac, updated_at: YYYY-MM-DD}`.
- Bump top-level `updated_at` when you edit.

## For mobile (later, optional)
`GET /api/board` returns `{"board": {...}}` — if you ever want a board tab/screen in the
app, it's the same shape. Not asked for yet; don't build until he asks.

— serv
