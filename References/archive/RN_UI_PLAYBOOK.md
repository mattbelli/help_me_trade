# RN UI Playbook — elite native-feel for the help_me_trade iOS app

*Companion to `RN_APP_BRIEF.md`. Evidence base from an adversarially-verified deep-research run
(~June 2026 reality: Expo SDK 55/56, iOS 26 / WWDC25). 25 claims verified, 13 confirmed / 12 killed.*

**How to read this:** items are tagged **[VERIFIED]** (survived 3-vote adversarial check, cited),
**[DIRECTIONAL]** (passed 2-1, treat as a lean not a law), or **[VERIFY FIRST-PARTY]** (the web evidence
was thin or refuted — confirm against primary docs before you build on it). This area moves fast — the
Live Activity tooling changed *weeks* before this was written, so **anything you read elsewhere older
than mid-2026 is probably stale.** Re-verify before committing the architecture.

---

## 0. TL;DR — the load-bearing realities
1. **The killer feature is shippable today** — approve a trade from the Dynamic Island / Lock Screen via
   an **interactive Live Activity** (SwiftUI `Button`/`Toggle` backed by **AppIntents**). But the RN path
   churned: prefer the **official `expo-widgets` (stable SDK 56)** over the now-**deprecated**
   `expo-live-activity`; **Voltra** (Callstack) is a viable alternative. All require an **Expo Dev Client
   + prebuild** (no Expo Go), and **you will write some Swift** for the widget extension.
2. **The extension can't touch the network.** An AppIntent button tap **briefly wakes the host app**,
   and the *app* makes the approval call — which still hits our server chokepoint (RiskEngine gates it).
3. **Polish = keep work on the UI thread.** Reanimated worklets, animate `transform`/`opacity` (never
   layout props), never read `sv.value` from the JS thread in a hot path, FlashList over FlatList.
4. **Expo Router's Apple zoom transition is alpha** with a ~1s latency bug — don't use it for the
   high-frequency ticker→detail tap; use a standard fast push / shared-element instead.

---

## 1. The killer feature — Live Activities / Dynamic Island approve flow
*(The most important and best-verified section.)*

**What's shippable** [VERIFIED]: interactive Live Activities with approve/reject **buttons** are real
(SwiftUI `Button`/`Toggle` + the **AppIntents** framework). Source: Apple ActivityKit docs.

**RN tooling — current state** [VERIFIED]:
- `expo-live-activity` (software-mansion-labs) was **archived/deprecated 2026-06-01** — its own README
  says "deprecated, consider expo-widgets." Don't start here.
- **`expo-widgets`** graduated to **stable in SDK 56** (~May 2026); supports Live Activities + home-screen
  widgets. **Likely the default choice.**
- **Voltra** (callstackincubator, v1.4.0, actively maintained) generates native SwiftUI/Glance targets —
  viable alternative. ⚠️ The claim that Voltra writes Live Activity UI **in React JSX (auto-generating
  SwiftUI)** was **REFUTED** — do **not** assume zero-Swift. Budget for authoring the native widget
  extension in Swift.
- All paths require **Expo Dev Client** + `npx expo prebuild --clean` (CNG). **Expo Go won't work.**

**The approve mechanism** [VERIFIED]: the Live Activity extension **cannot access the network or
location** (own sandbox). Realistic flow: **approve button → fires an AppIntent → briefly wakes the host
app in the background → the app calls our `/api/proposals/{id}/approve` (with `payload_hash`) → server
chokepoint/RiskEngine executes.** Dynamic updates to the activity come from the foreground app **or**
ActivityKit→APNs push. Note: Live Activity buttons are **non-interactive in CarPlay** (iOS 26).

**Lifetime budget** [VERIFIED]: **8h active + up to 4h more on the Lock Screen = 12h max.** A
pending-approval activity is **not** guaranteed to persist a full trading day — for long-pending
proposals, plan to refresh/restart the activity or fall back to a standard actionable push notification.
(Also: our proposals already carry a TTL ≤ 1h, so this rarely bites — but design for it.)

**[VERIFY FIRST-PARTY] before architecting the server push:**
- The exact **push-to-start token vs per-activity update token** flow and the **minimum iOS** for each
  (the commonly-cited "16.2 min / 17.2 push-to-start" was **REFUTED as stated** — confirm against current
  ActivityKit docs).
- Whether `expo-widgets` / Voltra expose interactive **AppIntent buttons + the Dynamic Island
  compact/expanded/minimal presentations** end-to-end from RN, or whether hand-written Swift in the
  widget extension is still required for the approve/reject buttons.

> Server implication for us: the push-delivery slice (device tokens + ActivityKit→APNs) in
> `trading/proposals.py` must drive *start* + *update* + *end* of the activity. Spike this **first** in
> the plan — it's the riskiest, highest-value path.

## 2. RN/Expo polish toolkit (verified)
- **Reanimated runs animations on the UI thread via worklets** [VERIFIED] — the core mechanism for
  jank-free gestures (paging, drag-reorder, chart scrubbing). "Natively" = on-UI-thread, not compiled
  machine code. ⚠️ Do **not** promise universal **120fps** — that marketing claim was **REFUTED**; target
  ProMotion where the device + render path allow and **measure**.
- **Animate `transform`/`opacity`, never layout props** [VERIFIED] — `top/left/width/height/margin/padding`
  force per-frame layout recalculation. Build drag-reorder and page-swipes on `translateX/translateY/scale`.
- **Never read `sv.value` from the JS runtime in a hot path** [VERIFIED] — it blocks the JS thread until
  the UI thread responds (a profiler trace showed `executeOnUIRuntimeSync` blocking >500ms). Do P&L /
  "odometer" ticks via **derived values inside worklets**, not JS-side reads.
- **FlashList over FlatList** for watchlist + activity feed [VERIFIED] — component recycling, vendor "up
  to 5x UI / 10x JS thread" (on a low-end Android; "up to" = peak, not guarantee). **Caveats:** provide
  `estimatedItemSize` + stable keys; recycling can blank during very fast scroll. ⚠️ FlashList **v2**
  specific marketing claims (50% less blank area, New-Arch synchronous layout, memory-bloat avoidance)
  were **REFUTED** — benchmark on target iOS hardware, don't trust v2 perf promises.
- **Expo Router Apple zoom transition** [VERIFIED]: SDK 55+, **iOS-only, alpha**, `Link.AppleZoom`
  (+ `Link.AppleZoomTarget`); the zoom effect needs iOS 18+ (graceful fallback below). **Known ~1s
  latency bug** on rapid open/close (upstream `react-native-screens#3621`, `expo/expo#42797`). **Tapping a
  watchlist ticker is high-frequency → default to a standard fast push / shared-element**; reserve zoom
  for low-frequency hero navigations only, or until the upstream bug is fixed.

## 3. Elite-feel craft — **honest status: source from Apple HIG + direct teardown**
The deep-research run **could not** substantiate citable, specific craft constants (spring/easing values,
exact haptic mappings, type ramps) — the available web sources (e.g. Robinhood design critiques) were
blog-quality and got **refuted**. So treat sections 1 & 4 of the original brief as **not settled by
secondary sources.** Get them from the primary, authoritative places instead:
- **Apple Human Interface Guidelines** — *Motion*, *Playing haptics* (`UIImpactFeedbackGenerator` /
  `UINotificationFeedbackGenerator` semantics), *Typography*, *Materials*, *Dark Mode*. These are the
  ground truth for native feel; mirror them, don't reinvent.
- **Direct teardown of the named exemplars** (the agent should do this in plan/spike): **Apple Stocks**
  (detail page + sparkline feel), **Apple Fitness** (rings as glanceable performance), **Apple Health**
  (summary density), **Robinhood / Cash App** (lightweight quick-buy flow), **Copilot Money** (investment
  performance presentation), **Linear / Things 3 / Arc Search** (motion + micro-interaction craft).
  *Teardown protocol:* screen-record at 240fps, note transition durations/curves, when each haptic fires,
  type sizes/weights, spacing rhythm, and every loading/empty/error/optimistic state. Encode the findings
  as design tokens (durations, springs, haptic map, type scale) before building.

[VERIFY FIRST-PARTY] The concrete craft specifics (spring constants, haptic vocabulary, type ramp,
optimistic-update patterns) for the exemplars — establish these via HIG + teardown, not blog posts.

## 4. Performance budget — "an RN app" vs "an app that happens to be RN"
The gap is almost entirely **what runs where**:
- All gesture-driven motion on the **UI thread** (Reanimated worklets + Gesture Handler), `transform`/
  `opacity` only.
- Lists on **FlashList** with `estimatedItemSize` + stable keys.
- No `sv.value` reads on the JS thread in hot paths; derive values in worklets.
- Measure on real ProMotion hardware; don't assume 120fps — confirm per render path.
- Live data (quotes, P&L) → push deltas into worklet-driven animations, not React re-renders per tick.

## 5. Anti-patterns (derive from the verified rules above)
*(The web "anti-pattern" lists were thin/refuted — these are the defensible ones from primary evidence.)*
- Animating layout props (`top/left/width/height`) instead of `transform`/`opacity`. ❌
- Reading `sv.value` from JS in a hot loop (blocks the JS thread). ❌
- FlatList for long live lists; FlashList without `estimatedItemSize`/stable keys. ❌
- Promising/depending on universal 120fps. ❌
- Assuming a Live Activity persists all day (12h cap), or that the extension can fetch data. ❌
- Adopting the alpha zoom transition for high-frequency navigation (the ~1s bug). ❌
- Assuming zero-Swift for Live Activities (Voltra JSX→SwiftUI codegen was refuted). ❌

## 6. Quality bar / checklist for the RN agent's plan
- [ ] Live Activity approve/reject end-to-end on **paper**: AppIntent button → app wake → `/approve`
      (payload_hash) → chokepoint; activity start/update/end driven by the server push slice.
- [ ] Dynamic Island compact/expanded/minimal presentations designed + implemented.
- [ ] Gesture paging (center-default Home) + hold-drag-reorder watchlist, all UI-thread, `transform`-based.
- [ ] FlashList everywhere a list can grow; `estimatedItemSize` + stable keys; measured for blanking.
- [ ] Ticker→detail uses a 60/120fps push/shared-element (NOT the alpha zoom) until upstream fix.
- [ ] Design tokens (durations, springs, haptic map, type scale) extracted from HIG + teardown, applied
      consistently.
- [ ] Every screen has considered loading/skeleton/empty/error states + optimistic updates.
- [ ] Measured on real ProMotion device; no JS-thread reads in hot paths; no layout-prop animations.

## 7. Recommended libraries (current-as-of ~June 2026 — re-verify)
- **Animation/gesture:** `react-native-reanimated` (3/4), `react-native-gesture-handler`. [VERIFIED core]
- **Lists:** `@shopify/flash-list` (mind v2 caveats). [VERIFIED w/ caveats]
- **Live Activities / widgets:** **`expo-widgets` (SDK 56 stable)** preferred; **Voltra** alternative;
  **avoid** deprecated `expo-live-activity`. Expo Dev Client + prebuild required. [VERIFIED]
- **Navigation/transitions:** Expo Router; treat its Apple zoom transition as opt-in/alpha. [VERIFIED]
- **Charts:** evaluate `react-native-skia` for custom sparklines/charts (not separately verified here —
  spike it). [VERIFY FIRST-PARTY]
- **Build:** EAS Build + Dev Client (mandatory for the native widget extension). [VERIFIED]

## 8. Myths refuted in verification — do NOT carry forward
- Voltra generates Live Activity UI from React JSX (no Swift). **FALSE.**
- Live Activities min iOS 16.2 / push-to-start 17.2 (as stated). **REFUTED — re-check.**
- FlashList v2: 50% less blank area / New-Arch synchronous layout / avoids memory bloat. **REFUTED.**
- Reanimated does "120fps and beyond on iOS/Android/Web." **REFUTED (overreach).**
- Robinhood-critique specifics (odometer price animation as best-practice; split Buy/Sell/Options
  buttons). **REFUTED (blog-quality).**
- Various `reanimated-dnd` specifics (60fps guarantee, New-Arch requirement, onDrop-only state). **REFUTED.**

## 9. Open questions to resolve first-party (before committing architecture)
1. Exact ActivityKit **push-to-start vs per-activity update token** flow + minimum iOS + the concrete
   APNs payload to start/update a trade-approval activity from our server.
2. Do `expo-widgets`/Voltra expose interactive **AppIntent buttons + Dynamic Island presentations**
   end-to-end from RN, or is hand-written Swift still required?
3. The concrete **elite-feel craft tokens** for the exemplars (HIG + teardown).
4. The best-in-class **60/120fps ticker→detail transition** in current Expo Router given the zoom bug.

---

### Sources (primary, verified)
- Apple ActivityKit — *Displaying live data with Live Activities*:
  https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities
- WWDC25 session 216 (CarPlay / Live Activities): https://developer.apple.com/videos/play/wwdc2025/216/
- Reanimated docs + performance guide: https://docs.swmansion.com/react-native-reanimated/ ·
  https://docs.swmansion.com/react-native-reanimated/docs/guides/performance/
- FlashList: https://shopify.github.io/flash-list/
- Expo Router zoom transition: https://docs.expo.dev/router/advanced/zoom-transition/
- expo-live-activity (deprecated): https://github.com/software-mansion-labs/expo-live-activity ·
  expo-widgets: https://docs.expo.dev/versions/latest/sdk/widgets/ · Voltra:
  https://github.com/callstackincubator/voltra
- Apple HIG (for §3, craft): Motion / Playing haptics / Typography (developer.apple.com/design).
