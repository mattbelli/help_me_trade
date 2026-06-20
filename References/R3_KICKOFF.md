# R3 kickoff — phone delivery (push approval)

*A paste-able brief to start a fresh agent on R3. Read first: `HANDOFF.md`, `CLAUDE.md` (mission + working
agreements), `AGENTS.md` (codebase map), `WATCHLIST_SPEC.md` (the "Phone viewing does NOT require R3" note),
`BUILD.md` Realignment §R3.*

---

You're picking up **R3 — phone delivery**, the next focus after the Linux-server migration (now done; the app
runs 24/7 on `serv` and is viewable from any tailnet device at http://serv.tail78aab8.ts.net:3001).

**Goal.** When the agent proposes a trade, the founder gets a **push notification on his phone** and can
**Approve / Reject / Modify** it from the phone. On approve, the order flows through the **existing** execution
chokepoint (`RiskEngine` → `ExecutionService` — the sole broker path; **never add a new one**). This is the
"be a dad" unlock: success = how little he has to watch it.

**Already built — do NOT rebuild:** `propose_order` (card UI), the fail-closed chokepoint, paper execution
(buy-to-open + sell-to-close, equities + long options), Tailscale access, the responsive web UI (already
phone-viewable). R3 adds only the **delivery + remote-approval** layer on top.

**FIRST, lock the delivery mechanism with the founder (don't skip):**
- **(A) PWA + Web Push — recommended MVP.** Installable PWA ("Add to Home Screen"); Web Push for the approve
  flow. iOS 16.4+ supports Web Push for *installed* PWAs. No Mac, no Xcode, no App Store, no Apple Developer
  account. **Prereq:** push + service workers require a **secure context (HTTPS)** — plain http over Tailscale
  won't qualify, so enable **`tailscale serve`** (HTTPS on the `*.ts.net` name) in front of the frontend.
- **(B) Capacitor + self-hosted APNs — heavier.** Native iOS wrapper + direct APNs. More reliable push, but
  needs **macOS + Xcode** to build iOS and an **Apple Developer account** ($99/yr). It was the original plan,
  but it's high-friction for a solo dev on Linux/Windows.

Recommend **(A)** for the MVP; keep **(B)** as the upgrade path if Web Push proves too limited on iOS.

**Then build it — brief → Composer (one run at a time); Sonnet review on anything touching execution/risk:**
1. **Secure context:** put the frontend behind `tailscale serve` HTTPS; update `NEXT_PUBLIC_API_URL` / CORS
   (`FRONTEND_ORIGIN`) / `BIND_HOST` to the https origin; rebuild + restart `hmt-frontend`; verify end-to-end.
2. **PWA shell:** web manifest + service worker + install prompt; verify "Add to Home Screen" on the iPhone.
3. **Push subscription:** VAPID keys in `.env`; register the device; persist the subscription server-side
   (follow the `db/ai_tables.py` table patterns).
4. **Proposal → push:** in the DELIVER stage, when the agent proposes an order, send a Web Push with
   Approve/Reject/Modify actions that deep-link into the app.
5. **Remote-approval endpoint:** authenticated; Approve routes through the **same** `ExecutionService`
   chokepoint; Modify re-opens the proposal card. **Adversarial Sonnet review before commit.**
6. **Honesty:** it only works when `serv` is reachable 24/7 (it is) and the phone is on the tailnet.

**Guardrails (from `CLAUDE.md`/`AGENTS.md`):** money is `Decimal`; never expose the `trading` toolset to chat;
LLM proposes, deterministic code disposes; **LIVE stays hard-blocked** (still paper). Pre-push: `ruff check .`
+ `pytest` + `cd frontend && npm run typecheck`.

**Start by** confirming (A) vs (B) with the founder, then write brief #1 (`tailscale serve` HTTPS + PWA shell).
