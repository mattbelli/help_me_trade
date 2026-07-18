# CHAT_RESTYLE_RECON — Web chat → mobile visual language

*Produced by recon agent 2026-06-30. All file:line citations reference the files as they currently exist on `main`.*

---

## 1. Files to edit

| File | Role |
|------|------|
| `frontend/components/assistant-ui/thread.tsx` | Thread, messages, bubbles, empty state, composer, indicator |
| `frontend/components/assistant-ui/tool-group.tsx` | Tool-running hint while agent works |
| `frontend/components/agents/order-proposal-card.tsx` | Inline order approval card |
| `frontend/components/agents/rule-proposal-card.tsx` | Inline rule approval card |
| `frontend/components/agents/propose-order-tool-ui.tsx` | Post-approval status banners (success/rejected/processing) |
| `frontend/components/agents/propose-rule-tool-ui.tsx` | Post-approval status banners |
| `frontend/app/globals.css` | Add `@keyframes breathe` for typing-dot animation |

`frontend/components/ai-elements/message.tsx` is a separate component used elsewhere (not by the `assistant-ui` Thread path); it is NOT in the thread render path and does not need restyle. Skip it.

---

## 2. Shared palette — what's already in globals.css

All Carbon tokens are already declared in `:root` / `[data-theme="carbon"]` (lines 58–173). The restyle only needs to reference these existing vars:

| Semantic purpose | CSS var | Carbon value |
|-----------------|---------|--------------|
| Accent / teal | `--c-accent` | `#5ad1be` |
| Accent-wash fill (user bubble, proposal card bg) | `--c-accent-soft` | `rgba(90,209,190,0.12)` |
| Text on accent background | `--c-on-accent` | `#0b0d10` |
| Surface / card bg (assistant bubble, composer) | `--bg-raised` | `#181b21` |
| Page background | `--background` | `#131519` |
| Hairline border | `--border` | `#262b33` |
| Stronger border / divider | `--border-strong` | `#333a44` |
| Dimmed text (textMuted) | `--fg-dim` | `#9aa1ab` |
| Faint text (textFaint) | `--fg-faint` | `#616873` |
| Positive green | `--c-green` | `#5bc98a` |
| Negative red | `--c-red` | `#ef6f6c` |

Mono font available via Tailwind: `font-mono` maps to `--font-mono` (line 14 `@theme inline`).

The `--c-accent-soft` var is the exact equivalent of the mobile `colors.accentWash`. The `--bg-raised` matches `colors.surface`. These are the only two fills the bubble restyle requires.

---

## 3. Mobile reference — extracted concrete values

Source: `feat/mobile-app` branch, `mobile/src/features/chat/`.

### Bubble geometry (chat-bubbles.tsx)
- `maxWidth: '88%'`
- `borderRadius: radius.xl` = **22px** (all corners default)
- Asymmetric tail: **square one bottom corner**, smooth all others
  - User bubble: `borderBottomRightRadius: radius.sm` = **8px** (tail on sender's side)
  - Assistant bubble: `borderBottomLeftRadius: radius.sm` = **8px** (tail on assistant's side)
- `paddingHorizontal: spacing.lg` = **16px**; `paddingVertical: spacing.md` = **12px**
- User fill: `colors.accentWash` (= `--c-accent-soft`)
- User border: `'transparent'`
- Assistant fill: `colors.surface` (= `--bg-raised`)
- Assistant border: `colors.border` (= `--border`), `borderWidth: hairline`

### Typing dots (chat-bubbles.tsx `TypingDots`)
- Three 7×7px circles, `borderRadius: 4`, `backgroundColor: colors.textMuted` (= `--fg-dim`)
- Gap: 5px between dots; `paddingVertical: 3`
- Each dot animates `opacity`: 0.3 → 1 → 0.3, duration 380ms per leg
- Delays: 0ms, 160ms, 320ms

### Tool hint / pulsing eyebrow (chat-bubbles.tsx `ToolHint`)
- Rendered as `<Eyebrow color="accent">{label}…</Eyebrow>` while a tool runs
- Eyebrow spec (text.tsx lines 100–113): `fontFamily: fonts.mono`, `fontSize: 11`, `letterSpacing: 1.6`, `textTransform: 'lowercase'`
- Opacity pulses 0.5 → 1 → 0.5 (600ms per leg)
- Shown INSIDE the assistant bubble, above the content

### Empty state (chat-screen.tsx `EmptyState`)
- Center icon: 64×64 circle, `backgroundColor: colors.accentWash`, chat icon 30px in `colors.accent`
- Title: `variant="title"` (28px, 700, sans, tight tracking)
- Subtitle: `variant="callout"` (14px, 500) in `color="textMuted"`, centered
- Suggestions: full-width rows (not pills) — `borderWidth: hairline`, `borderColor: colors.border`, `backgroundColor: colors.surface`, `borderRadius: radius.lg` (16px), `paddingHorizontal: 16`, `paddingVertical: 12`
  - Each row: text left-aligned in `textMuted`, chevron-right icon faint on the right

### Composer (chat-screen.tsx)
- Input wrapper: `backgroundColor: colors.surface`, `borderWidth: hairline`, `borderColor: colors.border`, `borderRadius: radius.xl` (22px), `paddingHorizontal: 16`, `paddingVertical: 12`
- Send/stop button: **44×44**, `borderRadius: radius.pill` (999)
  - Idle/send: `backgroundColor: colors.accent`, icon `colors.onAccent`, opacity 0.4 if empty
  - Streaming/stop: `backgroundColor: colors.surfaceRaised`, icon `colors.textMuted`

### Proposal hand-off card (chat-bubbles.tsx `ProposalHandoff`)
- Container: `borderRadius: radius.lg` (16px), `borderWidth: hairline`, `borderColor: colors.accent`, `backgroundColor: colors.accentWash`
- Padding: `paddingHorizontal: 12`, `paddingVertical: 12`
- Interior: row with 34×34 surface-bg icon circle (`borderRadius: radius.md`=12), inbox icon in accent
- Stack: Eyebrow "proposal ready" in accent + bold summary line + caption "Review & approve in Approvals"
- Trailing chevron in accent

### Eyebrow spec (text.tsx)
```
fontFamily: fonts.mono   (Menlo / monospace)
fontSize: 11
letterSpacing: 1.6       (≈ tracking-[0.1em] in Tailwind)
textTransform: lowercase
color: textFaint (default) or accent (for tool hints, proposal eyebrow)
```

---

## 4. Per-component CURRENT → DESIRED restyle map

### A. User bubble — `thread.tsx` lines 523–526

**CURRENT:**
```tsx
// line 524
<div className="aui-user-message-content peer bg-muted text-foreground rounded-xl px-4 py-2 wrap-break-word empty:hidden">
```
- Fill: `bg-muted` = `--muted` = `#1e2228` (generic muted, not accent)
- Corners: `rounded-xl` = uniform (no tail)
- Padding: `py-2` = 8px vertical

**DESIRED:**
```tsx
<div className="aui-user-message-content peer bg-[var(--c-accent-soft)] text-foreground
  rounded-[22px] rounded-br-[8px]
  px-4 py-3 border border-transparent
  wrap-break-word empty:hidden">
```
- Fill → `bg-[var(--c-accent-soft)]` (accent-wash)
- Corners → `rounded-[22px]` everywhere + `rounded-br-[8px]` for tail (squaring bottom-right)
- Vertical padding → `py-3` (12px, matches mobile `spacing.md`)
- Add `border border-transparent` (mirrors mobile structure; keeps layout consistent with assistant bubble)

Also add `max-w-[88%]` to the wrapper at line 523:
```tsx
// line 523
<div className="aui-user-message-content-wrapper relative col-start-2 min-w-0 max-w-[88%] ml-auto">
```

---

### B. Assistant bubble — `thread.tsx` lines 376–461

**CURRENT:**
```tsx
// line 384
<div data-slot="aui_assistant-message-content"
  className="text-foreground px-2 leading-relaxed wrap-break-word [contain-intrinsic-size:auto_24px] [content-visibility:auto]">
```
No background, no border, no card. Raw left-aligned text.

**DESIRED:** Wrap in a surface card:
```tsx
<div data-slot="aui_assistant-message-content"
  className="text-foreground
    bg-[var(--bg-raised)] border border-[var(--border)]
    rounded-[22px] rounded-bl-[8px]
    px-4 py-3 max-w-[88%]
    leading-relaxed wrap-break-word
    [contain-intrinsic-size:auto_24px] [content-visibility:auto]">
```
- Background: `bg-[var(--bg-raised)]` (surface card)
- Border: `border border-[var(--border)]` (hairline)
- Corners: `rounded-[22px] rounded-bl-[8px]` (tail on bottom-left, assistant side)
- Padding: `px-4 py-3`
- Max-width: `max-w-[88%]`

The `TurnLearnedMemories` and footer action bar at lines 452–460 sit OUTSIDE the bubble div, so they remain unaffected. The `ms-2` offset on the footer (line 456) may need to become `ms-0` or `mt-1` since the bubble now has padding.

---

### C. Typing dots / streaming indicator — `thread.tsx` lines 434–442

**CURRENT** (the `"indicator"` case in `GroupedParts`):
```tsx
// line 436
<span
  data-slot="aui_assistant-message-indicator"
  className="animate-pulse font-sans"
  aria-label="Assistant is working"
>●</span>
```
Single pulsing dot, sans.

**DESIRED:** Three breathing dots (mobile `TypingDots`):
```tsx
<span data-slot="aui_assistant-message-indicator"
  aria-label="Assistant is working"
  className="flex items-center gap-[5px] py-[3px]">
  <span className="w-[7px] h-[7px] rounded-full bg-[var(--fg-dim)] animate-[breathe_1.14s_ease-in-out_0s_infinite]" />
  <span className="w-[7px] h-[7px] rounded-full bg-[var(--fg-dim)] animate-[breathe_1.14s_ease-in-out_160ms_infinite]" />
  <span className="w-[7px] h-[7px] rounded-full bg-[var(--fg-dim)] animate-[breathe_1.14s_ease-in-out_320ms_infinite]" />
</span>
```
Requires adding to **`frontend/app/globals.css`**:
```css
@keyframes breathe {
  0%, 100% { opacity: 0.3; }
  50%       { opacity: 1;   }
}
```

---

### D. Tool-running hint — `tool-group.tsx` lines 95–155

**CURRENT** (ToolGroupTrigger, active state):
```tsx
// line 119
{active && <LoaderIcon className="aui-tool-group-trigger-loader size-4 shrink-0 animate-spin" />}
// line 135
{active && <span aria-hidden className="aui-tool-group-trigger-shimmer shimmer pointer-events-none absolute inset-0 ...">…</span>}
```
Uses a spinner + shimmer text overlay.

**DESIRED:** Replace the shimmer text span with a mono-eyebrow pulse treatment; keep the spinner as a compact prefix indicator or drop it. The trigger label itself should pulse in accent color while active:
```tsx
// Replace shimmer span with:
{active && (
  <span
    aria-hidden
    className="pointer-events-none absolute inset-0
      font-mono text-[11px] tracking-[0.1em] lowercase
      text-[var(--c-accent)] animate-pulse"
  >
    {label}…
  </span>
)}
```
The `LoaderIcon` spinner can remain or be removed — the eyebrow pulse is the primary affordance.

Alternatively (closer to mobile's ToolHint pattern): don't show a trigger at all while streaming; just render the eyebrow text inline in `AssistantMessage` above the content. But that requires changes to how `thread.tsx` passes the `ToolGroup` prop — the current approach of restyling the trigger is lower-risk.

---

### E. Empty state — `thread.tsx` lines 203–237

**CURRENT (ThreadWelcome, line 203):**
```tsx
<div className="aui-thread-welcome-root mb-6 flex flex-col items-center px-4 text-center">
  <h1 className="... text-2xl font-semibold duration-200">How can I help you today?</h1>
</div>
```
No icon, no subtitle.

**CURRENT (ThreadSuggestions / ThreadSuggestionItem, lines 213–236):**
Wrapped buttons with `rounded-full border px-3.5 py-1.5` — horizontal pill style.

**DESIRED (ThreadWelcome):**
```tsx
<div className="aui-thread-welcome-root mb-6 flex flex-col items-center gap-4 px-4 text-center">
  {/* Accent icon circle */}
  <div className="flex size-16 items-center justify-center rounded-full bg-[var(--c-accent-soft)]">
    <BotMessageSquareIcon className="size-7 text-[var(--c-accent)]" />
  </div>
  {/* Title */}
  <div className="flex flex-col items-center gap-2">
    <h1 className="... text-2xl font-semibold">Ask the agent</h1>
    <p className="text-sm text-[var(--fg-dim)] max-w-xs">
      Its thesis, your portfolio, what it&apos;s watching — and when it&apos;s worth a trade.
    </p>
  </div>
</div>
```

**DESIRED (ThreadSuggestionItem, line 223):** Replace pill buttons with full-width surface rows:
```tsx
<SuggestionPrimitive.Trigger send asChild>
  <button className="aui-thread-welcome-suggestion
    flex w-full items-center justify-between gap-2
    rounded-[16px] border border-[var(--border)]
    bg-[var(--bg-raised)]
    px-4 py-3 text-sm text-[var(--fg-dim)]
    hover:bg-[var(--bg-hover)] transition-colors text-left">
    <SuggestionPrimitive.Title />
    <ChevronRightIcon className="size-4 shrink-0 text-[var(--fg-faint)]" />
  </button>
</SuggestionPrimitive.Trigger>
```
Also update ThreadSuggestions wrapper (line 214) to `flex-col` instead of `flex-wrap justify-center` so rows stack vertically.

Update the suggestion strings in `frontend/app/` wherever they're configured (or pass via `<Thread>` props) to match:
- "What was my unrealized P&L today?"
- "What trades were made today?"
- "What's my year-to-date performance?"

---

### F. Composer — `thread.tsx` lines 239–275, 277–349

**CURRENT (composer shell, line 247):**
```
bg-background border-border/60 ... rounded-3xl border p-(--composer-padding) shadow-[...]
```
**CURRENT (send button, line 320):** `size-7 rounded-full` (28px) — too small vs mobile's 44px
**CURRENT (cancel/stop, line 335):** `size-7 rounded-full`

**DESIRED (composer shell):**
```
bg-[var(--bg-raised)] border-[var(--border)] ... rounded-[22px] border p-(--composer-padding)
```
Keep the shadow and focus-within styles.

**DESIRED (send button, line 320–332):**
```tsx
<TooltipIconButton
  className="aui-composer-send size-11 rounded-full bg-[var(--c-accent)] text-[var(--c-on-accent)]
    hover:opacity-90 disabled:opacity-40 transition-opacity"
  ...
>
```
Remove `variant="default"` — use explicit accent background class instead.

**DESIRED (cancel/stop button, lines 335–347):**
```tsx
<Button
  className="aui-composer-cancel size-11 rounded-full bg-[var(--bg-raised)]
    border border-[var(--border)] text-[var(--fg-dim)]"
  ...
>
```

---

### G. Proposal cards — `order-proposal-card.tsx` + `rule-proposal-card.tsx`

**CURRENT (both cards, e.g. order-proposal-card.tsx line 94):**
```
rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm shadow-sm
```
Amber/golden — generic "warning" color.

**DESIRED:** Match mobile "proposal ready" card look.

Replace the outer div class with:
```
rounded-[16px] border border-[var(--c-accent)] bg-[var(--c-accent-soft)] p-3 text-sm
```

Add a mono eyebrow **above** the title paragraph:
```tsx
<p className="font-mono text-[11px] tracking-[0.1em] lowercase text-[var(--c-accent)] mb-1">
  proposal ready
</p>
```

Add a **side badge** next to the "Paper order" title for order proposals:
```tsx
<span className={cn(
  "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-mono lowercase ml-2",
  isSell
    ? "bg-[var(--c-red)]/15 text-[var(--c-red)]"
    : "bg-[var(--c-green)]/15 text-[var(--c-green)]"
)}>
  {side.toLowerCase()}
</span>
```

The amber border in the dl divider (line 158: `border-amber-500/20`) → `border-[var(--border)]`.

**Keep** the Approve/Reject buttons at the bottom — only the card shell changes.

---

### H. Post-approval status banners — `propose-order-tool-ui.tsx` + `propose-rule-tool-ui.tsx`

**CURRENT (`ProposalStatusBanner`):**
- `processing`: `border-amber-500/30 bg-amber-500/5` (amber)
- `dry_run`: `border-amber-500/40 bg-amber-500/10`

**DESIRED:** Replace amber-for-processing with accent-soft (teal wash matches the proposal card):
```
processing: "border-[var(--c-accent)]/40 bg-[var(--c-accent-soft)] text-foreground"
dry_run: "border-[var(--fg-dim)]/30 bg-[var(--bg-raised)] text-foreground"
```
Keep success (emerald) and rejected (destructive) — those are semantically correct and not amber.

---

## 5. Genuine forks to flag

1. **`@keyframes breathe` missing**: globals.css has no opacity-breathing keyframe. Must add it (or use Tailwind `animate-[breathe_...]` with an arbitrary `@keyframes` in globals.css). This is a 4-line addition with no risk.

2. **Assistant bubble card adds layout padding** — the current action bar footer is positioned `ms-2` (thread.tsx:456) relative to the message root. When the bubble gains `px-4 py-3` padding, the action bar will look inset from the card edge. Either:
   - Move action bar outside the bubble card (keep it as a sibling below), or
   - Accept the slight indent (it still works; just moves from raw-edge to card-edge alignment)
   
   The mobile has no action bar at all, so there's no mobile reference. Recommended: keep it as a sibling div below the bubble, shift `ms-0` → `mt-1 ms-0`.

3. **Max-width interaction with grid**: UserMessage uses `grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto]` (thread.tsx:518). The `max-w-[88%]` on the content wrapper works naturally since it's `col-start-2`. AssistantMessage uses no grid — `max-w-[88%]` on the content div works directly.

4. **Theme variants (ember, paper, light, dark)**: The `--c-accent-soft` and `--c-accent` vars exist in `carbon` and `ember` themes but NOT in `light`/`dark` themes (those use standard shadcn tokens). If those themes are used for the chat, the bubble fill will fall back to transparent. Mitigation: add `--c-accent-soft` and `--c-accent` to the `[data-theme="light"]` and `[data-theme="dark"]` blocks in globals.css (or use `bg-ring/10` as a fallback since `--ring` is set to the teal accent in both).

5. **Suggestion content strings** — the brief specifies new suggestion text ("What was my unrealized P&L today?" etc.) but the current ThreadSuggestions uses `<ThreadPrimitive.Suggestions>` which pulls from the assistant-ui runtime config, not from a hardcoded array. The suggestion strings live wherever the Thread is configured (search for `suggestions:` in the Next.js chat route or agent config). This is a data change, not a style change.

6. **Type ramp — no Eyebrow component on web**: The web has no `<Eyebrow>` primitive. The eyebrow treatment is implemented inline as a `<span>` with `font-mono text-[11px] tracking-[0.1em] lowercase text-[var(--c-accent)]`. This is fine for a restyle; no new component needed.

---

## 6. Quick summary table

| Component | File:line | CURRENT | DESIRED |
|-----------|-----------|---------|---------|
| User bubble fill | thread.tsx:524 | `bg-muted` (grey) | `bg-[var(--c-accent-soft)]` (teal wash) |
| User bubble corners | thread.tsx:524 | `rounded-xl` (uniform) | `rounded-[22px] rounded-br-[8px]` (tail) |
| User bubble padding | thread.tsx:524 | `py-2` | `py-3` |
| User bubble max-width | thread.tsx:523 | none | `max-w-[88%] ml-auto` on wrapper |
| Assistant bubble | thread.tsx:384 | no card | `bg-[var(--bg-raised)] border border-[var(--border)] rounded-[22px] rounded-bl-[8px] px-4 py-3 max-w-[88%]` |
| Streaming indicator | thread.tsx:436 | single `●` pulse | 3 breathing dots (7px, `--fg-dim`, staggered 0/160/320ms) |
| Tool-running hint | tool-group.tsx:135 | shimmer text overlay | pulsing mono-eyebrow (`font-mono text-[11px] tracking-[0.1em] lowercase text-[var(--c-accent)]`) |
| Empty state — icon | thread.tsx:203 | none | 64px circle `bg-[var(--c-accent-soft)]`, chat icon `text-[var(--c-accent)]` |
| Empty state — subtitle | thread.tsx:203 | none | `text-sm text-[var(--fg-dim)]` tagline |
| Suggestion rows | thread.tsx:228 | `rounded-full` pill buttons | `rounded-[16px]` full-width surface rows with chevron-right |
| Composer shell | thread.tsx:247 | `bg-background rounded-3xl` | `bg-[var(--bg-raised)] rounded-[22px]` |
| Send button size | thread.tsx:326 | `size-7` (28px) | `size-11` (44px) |
| Send button color | thread.tsx:326 | `variant="default"` | `bg-[var(--c-accent)] text-[var(--c-on-accent)]` |
| Stop button | thread.tsx:340 | `size-7 variant="default"` | `size-11 bg-[var(--bg-raised)] border border-[var(--border)]` |
| Proposal card shell | order-proposal-card.tsx:94, rule-proposal-card.tsx:127 | `border-amber-500/30 bg-amber-500/5` (amber) | `border-[var(--c-accent)] bg-[var(--c-accent-soft)]` + mono eyebrow |
| Proposal card eyebrow | (new) | none | `font-mono text-[11px] tracking-[0.1em] lowercase text-[var(--c-accent)]` "proposal ready" |
| Proposal card side badge | order-proposal-card.tsx | none | buy=green/15 teal wash pill, sell=red/15 pill |
| Processing status banner | propose-order-tool-ui.tsx:67 | `border-amber-500/30 bg-amber-500/5` | `border-[var(--c-accent)]/40 bg-[var(--c-accent-soft)]` |
| globals.css | (new keyframe) | no `breathe` keyframe | add `@keyframes breathe { 0%,100%{opacity:.3} 50%{opacity:1} }` |
