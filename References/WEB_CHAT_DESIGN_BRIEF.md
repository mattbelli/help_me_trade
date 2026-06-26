# Web chat → match the new mobile copilot's design language

*Founder ask: the RN Chat tab (`feat/mobile-app`, `mobile/src/features/chat/`) has a chat design he loves — make
the **web** copilot mirror its visual language. This is **restyling, not a rebuild**: the web chat is built on
**assistant-ui + ai-elements** (`frontend/components/assistant-ui/thread.tsx`, `frontend/components/ai-elements/
message.tsx`) with Tailwind, and it already reads the same "Carbon" palette the mobile chat was lifted from
(`frontend/app/globals.css` == mobile `src/theme/colors.ts`). So the color ground-truth is shared — the work is
theming assistant-ui's Thread / Message / Composer to match.*

## Mirror these specific treatments
- **Bubbles** — user = teal **accent-wash** fill, right-aligned, asymmetric tail (square the bottom-RIGHT corner;
  ~22px radius elsewhere); assistant = **surface** card + hairline border, left-aligned, square bottom-LEFT.
  Comfortable padding, max-width ~88%.
- **Type** — system sans for prose; **mono tabular** for any figures; lowercase, letter-spaced **mono "eyebrow"**
  labels for kickers (the app's terminal cue — same as `Eyebrow` in mobile `components/ui/text.tsx`).
- **Streaming affordances** — assistant text grows token-by-token; while a tool runs, a faint **pulsing mono
  eyebrow** ("reading context…"); before the first token, three **breathing dots**.
- **Empty state** — centered accent icon + title + a few **tappable suggestion rows** (e.g. "How is my portfolio
  doing today?", "What are you watching and why?", "What's my biggest risk right now?").
- **Composer** — rounded surface input; a round **accent send button** that swaps to a **stop** control while
  streaming.
- **Motion** — calm-but-alive: subtle fade-in on new messages, spring on press. Nothing flashy.

## ⚠️ Do NOT copy the proposal interaction — it's platform-specific
Mobile hands trade proposals **off** to the biometric Approvals screen (phones have Face ID; chat never approves
in-line). The web has **no biometrics** and already has working **inline Approve/Reject cards**
(`propose_order` / `propose_rule`). **Keep the web's inline approval** — only restyle it to the mobile
"proposal ready" card's *look*: accent-wash card, mono eyebrow `proposal ready`, bold summary line, a buy/sell
**side badge** (teal/positive for buy, negative for sell). Do not remove or biometric-gate the web approve.

## Reference (mobile source to match)
- `mobile/src/features/chat/chat-bubbles.tsx` — bubbles, the proposal hand-off card, typing dots, tool hint.
- `mobile/src/features/chat/chat-screen.tsx` — empty state + suggestions, composer, send↔stop.
- `mobile/src/components/ui/{text,layout,button}.tsx` + `src/theme/{colors,typography,spacing}.ts` — the tokens
  (Carbon palette, the `Eyebrow`/`Text` ramp, radii/spacing) the mobile chat is built from; the web equivalents
  live in `frontend/app/globals.css` + the Tailwind config.

*Scope note: this is web-cockpit polish, not the mission-critical path — sequence it behind the
`POST /api/chat/stream` endpoint (`References/SERV_BRIEF_chat_stream.md`), which unblocks the mobile chat on real
data.*
