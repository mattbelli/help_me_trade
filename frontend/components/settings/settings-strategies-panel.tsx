"use client";

import { useEffect, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";

import {
  TerminalButton,
  TerminalField,
  TerminalInput,
  TerminalToggle,
} from "@/components/ui/terminal";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchStrategies,
  fetchStrategyAccounts,
  fetchStrategyUniverses,
  updateStrategy,
  type Strategy,
  type StrategyAccount,
  type StrategyUniverse,
} from "@/lib/strategies-api";

// Base UI Select reserves no special meaning for values, but the codebase
// convention (see performance-page) is a sentinel string for a "none" option
// rather than an empty string. "" is the real unbound value sent to the API.
const UNBOUND = "__unbound__";

type StrategyDoc = {
  trigger: string;
  action: string;
  exit: string;
  status: { label: string; tone: "good" | "warn" | "danger"; note: string };
};

const STRATEGY_DOCS: Record<string, StrategyDoc> = {
  dip_buy: {
    trigger:
      "A healthy stock (above its 200-day average) that has sold off ~5%+ over 3 days while the broad market is also weak (SPY ~1.5%+ down over 3 days). Long only.",
    action: "Proposes buying the dip — you approve on your phone — sized to the notional.",
    exit:
      "Stop at −3%; at +3% it sells half and moves the stop to breakeven; trails the rest ~11%; force-exits after 20 trading days.",
    status: {
      label: "Validated",
      tone: "good",
      note: "Triple-validated, survivorship-free (~57% win rate) — the most battle-tested strategy here.",
    },
  },
  dip_refined: {
    trigger:
      "Same as Dip-Buy, but only when the stock is at least 5% above its nearest support level — skips dips sitting right on support, where the drop is likelier to continue.",
    action: "Proposes buying the dip (phone-approved), sized to the notional.",
    exit: "Same as Dip-Buy: stop −3% / +3% sell-half→breakeven / ~11% trail / 20-day cap.",
    status: {
      label: "Forward-test",
      tone: "warn",
      note: "Sharper recent excess return than Dip-Buy, but not yet block-bootstrap validated.",
    },
  },
  earnings_drift: {
    trigger:
      "A stock that gapped up 5%+ on its last earnings, entered ~10 trading days before its NEXT earnings (when earnings are 40–90 trading days apart). Long only.",
    action: "Proposes buying to ride the pre-earnings drift (phone-approved).",
    exit:
      "Sells ~1 trading day BEFORE the next earnings — captures the run-up, exits before the event. Max hold ~15 trading days.",
    status: {
      label: "Forward-test",
      tone: "warn",
      note: "Strong backtest Sharpe; combined with Dip it isn't overfit (PBO 0.18) — paper-forward is the real gate. Long-only (the short mirror had no edge).",
    },
  },
  session_node_spy: {
    trigger:
      "Intraday on SPY: when price tags a prior-session level (Value-Area High/Low or prior-day High/Low) it signals a snap back toward the prior session's Point of Control (POC). Tagging support (below POC) → long; tagging resistance (above POC) → short. Re-arms after price moves 2 points away; max 2 per level, 6 per day.",
    action: "Proposes a reversion trade targeting the POC (phone-approved).",
    exit: "Phone-approved exit (propose mode).",
    status: {
      label: "Exploratory",
      tone: "danger",
      note: "No validated edge yet — runs propose-only so you approve every action. Don't treat its signals as proven.",
    },
  },
  momentum_sp500: {
    trigger:
      "Monthly, on the last trading day: ranks the S&P 500 by 12-month momentum (skipping the most recent month), adjusted for each stock's sector, and targets the top 20% equal-weighted. A data-quality gate requires a full year of real bars per name and screens out extreme outliers; if fewer than 50 names survive, the whole rebalance aborts rather than trade bad data.",
    action:
      "AUTO-executes the monthly rebalance (this is the one auto strategy) — buys the new names, sells the dropped ones, dollar-weighted across the notional — then sends ONE summary push, not per-trade alerts.",
    exit:
      "Rebalance-managed only: a name is sold in full when it falls out of the top quintile. No stops, targets, or time caps — the portfolio IS the exit rule.",
    status: {
      label: "Forward-test",
      tone: "warn",
      note: "Backtest: +6.4%/yr Jensen alpha at 0.76 beta — but the stock-picking part of that alpha is fragile (loses significance without 2020/2026). Needs 2-3 clean monthly paper cycles before trusting.",
    },
  },
  steepyc_dip: {
    trigger:
      "Exactly the Dip-Buy signal (healthy stock above its 200-day average, down ~5%+ over 3 days while SPY is down ~1.5%+), but ONLY while the yield curve is meaningfully steep (10yr minus 2yr above +0.5%). Stale regime data = no trades (fail-closed).",
    action: "Auto-enters the dip within the notional, with a per-trade alert.",
    exit:
      "Pure 10-trading-day hold — no stop, no target, no trail. Time is the only exit.",
    status: {
      label: "Forward-test",
      tone: "warn",
      note: "Strong-but-unverified: ~+0.9%/trade raw excess, but about half of that is just recovering market beta; the standalone selection edge isn't statistically significant. Treat as tactical exposure, not stock-picking.",
    },
  },
  vix_spike: {
    trigger:
      "Buys SPY on a genuine volatility shock: VIX closes above 28 after having been calm (below 22 on at least 8 of the prior 12 days). The calm requirement filters out chronically-elevated-VIX regimes — this only fires on a spike out of quiet.",
    action: "Auto-buys SPY within the notional, with a per-trade alert.",
    exit:
      "Sells when VIX reverts below 20; hard time cap at 60 trading days regardless. Missing VIX data = hold (below the cap).",
    status: {
      label: "Pilot",
      tone: "warn",
      note: "Thin sample by nature: ~10 qualifying episodes in 16 years, ~+3.3%/episode. Real but rare — never a primary sleeve, and one bad episode moves the stats a lot.",
    },
  },
  generals_dip: {
    trigger:
      "Dip-buying restricted to 'the generals' — the top 30 most-traded names by dollar volume (refreshed monthly, point-in-time). Fires post-close when one of them is above its 50-day average but down 3.5%+ over 3 days. Max 5 positions at once.",
    action:
      "Proposes next-open entries — you approve each on your phone. Ships propose-only.",
    exit:
      "Pure 10-trading-day hold via the shared exit engine (no stop/target/trail). A monthly kill line auto-disables the sleeve if 6-month excess-vs-SPY goes negative or the trailing-60-trade win rate drops under 45%.",
    status: {
      label: "Regime-local",
      tone: "warn",
      note: "Tier-B: worked over the LAST 12 months (+3.6%/trade excess vs SPY, 60% wins, n=399) but flat over the full 5-year window — this edge is assumed to be regime-dependent and may stop. The kill line exists to catch that; no win% is ever shown on its cards by design.",
    },
  },
  vol_event: {
    trigger:
      "Two rare SPY panic-reversion setups: (1) Drawdown stabilization — SPY 5%+ below its 20-day high with VIX ≥ 25, entered on the FIRST day the selling stabilizes (higher close and higher low); (2) Term-structure flip — the first day the VIX/VIX3M ratio un-inverts after 2+ days inverted (near-term fear draining). One vol position at a time, shared with VIX-Spike (cluster cap).",
    action:
      "Proposes SPY entries — you approve each on your phone. Ships propose-only.",
    exit:
      "Drawdown entries exit when SPY recovers its prior 20-day high (30-trading-day cap); term-flip entries are a fixed 21-trading-day hold. Per-episode kill lines auto-disable a trigger after a −15% episode, 3 straight losers, or fading term-flip returns. Exits keep running even if you disable the sleeve.",
    status: {
      label: "Regime-local",
      tone: "warn",
      note: "Tier-B, episodic and small-n: post-2020, drawdown-stab won 8 of 10 (median +6.2%) and term-flip 13 of 14 (mean +5.0%) — genuinely encouraging, but a dozen episodes is description, not a win probability. Cards will never show a win% for it by design.",
    },
  },
};

function statusToneClasses(tone: StrategyDoc["status"]["tone"]): string {
  switch (tone) {
    case "good":
      return "border-[var(--c-green)]/30 bg-[var(--c-green-bg)]/10 text-[var(--c-green)]";
    case "warn":
      return "border-[var(--c-amber)]/30 bg-[var(--c-amber)]/10 text-[var(--c-amber)]";
    case "danger":
      return "border-destructive/40 bg-destructive/10 text-destructive";
  }
}

type StrategyDraft = {
  enabled: boolean;
  account_id: string;
  notional_usd: number;
  entry_mode: "auto" | "propose";
  exit_mode: "auto" | "propose";
  params: Record<string, unknown>;
  universe: string;
  max_concurrent_positions: number | null;
  max_deployed_usd: number | null;
};

type StrategyCardState = {
  draft: StrategyDraft;
  dirty: boolean;
  saving: boolean;
  toggling: boolean;
  error: string | null;
};

function strategyToDraft(strategy: Strategy): StrategyDraft {
  return {
    enabled: strategy.enabled,
    account_id: strategy.account_id ?? "",
    notional_usd: strategy.notional_usd,
    entry_mode: strategy.entry_mode ?? "propose",
    exit_mode: strategy.exit_mode,
    params: { ...strategy.params },
    universe: strategy.universe,
    max_concurrent_positions: strategy.max_concurrent_positions,
    max_deployed_usd: strategy.max_deployed_usd,
  };
}

function buildPatch(
  original: StrategyDraft,
  draft: StrategyDraft,
  hasFarSupportMin: boolean,
): Partial<StrategyDraft> {
  const patch: Partial<StrategyDraft> = {};

  if (draft.enabled !== original.enabled) patch.enabled = draft.enabled;
  if (draft.account_id !== original.account_id) patch.account_id = draft.account_id;
  if (draft.notional_usd !== original.notional_usd) {
    patch.notional_usd = draft.notional_usd;
  }
  if (draft.entry_mode !== original.entry_mode) patch.entry_mode = draft.entry_mode;
  if (draft.exit_mode !== original.exit_mode) patch.exit_mode = draft.exit_mode;
  if (draft.universe !== original.universe) patch.universe = draft.universe;
  if (draft.max_concurrent_positions !== original.max_concurrent_positions) {
    patch.max_concurrent_positions = draft.max_concurrent_positions;
  }
  if (draft.max_deployed_usd !== original.max_deployed_usd) {
    patch.max_deployed_usd = draft.max_deployed_usd;
  }

  if (hasFarSupportMin) {
    const origVal = Number(original.params.far_support_min);
    const draftVal = Number(draft.params.far_support_min);
    if (draftVal !== origVal) {
      patch.params = { ...draft.params };
    }
  }

  return patch;
}

function StrategyCard({
  strategy,
  accounts,
  universes,
  onSaved,
}: {
  strategy: Strategy;
  accounts: StrategyAccount[];
  universes: StrategyUniverse[];
  onSaved: (updated: Strategy) => void;
}) {
  const original = strategyToDraft(strategy);
  const hasFarSupportMin = "far_support_min" in strategy.params;

  const [expanded, setExpanded] = useState(false);
  const [notionalText, setNotionalText] = useState(String(strategy.notional_usd));
  const [farSupportText, setFarSupportText] = useState(
    hasFarSupportMin ? String(strategy.params.far_support_min) : "",
  );

  const [state, setState] = useState<StrategyCardState>(() => ({
    draft: original,
    dirty: false,
    saving: false,
    toggling: false,
    error: null,
  }));

  function updateDraft(updater: (draft: StrategyDraft) => StrategyDraft) {
    setState((prev) => {
      const nextDraft = updater(prev.draft);
      const nextOriginal = strategyToDraft(strategy);
      const patch = buildPatch(nextOriginal, nextDraft, hasFarSupportMin);
      const dirty = Object.keys(patch).length > 0;
      return { ...prev, draft: nextDraft, dirty, error: null };
    });
  }

  async function handleSave() {
    const nextOriginal = strategyToDraft(strategy);
    const patch = buildPatch(nextOriginal, state.draft, hasFarSupportMin);
    if (Object.keys(patch).length === 0) return;

    setState((prev) => ({ ...prev, saving: true, error: null }));
    try {
      const updated = await updateStrategy(strategy.id, patch);
      onSaved(updated);
    } catch (err) {
      setState((prev) => ({
        ...prev,
        saving: false,
        error: err instanceof Error ? err.message : "Failed to save strategy",
      }));
    }
  }

  async function handleToggle(checked: boolean) {
    // Optimistic: flip the local draft immediately so the Switch feels instant.
    setState((prev) => ({
      ...prev,
      draft: { ...prev.draft, enabled: checked },
      toggling: true,
      error: null,
    }));
    try {
      const updated = await updateStrategy(strategy.id, { enabled: checked });
      // onSaved updates the parent list and re-keys this card, resetting state.
      onSaved(updated);
    } catch (err) {
      // Revert optimistic update.
      setState((prev) => ({
        ...prev,
        draft: { ...prev.draft, enabled: !checked },
        toggling: false,
        error: err instanceof Error ? err.message : "Failed to update strategy",
      }));
    }
  }

  const { draft, dirty, saving, toggling, error } = state;
  const doc = STRATEGY_DOCS[strategy.id];
  const isSessionNode = strategy.id === "session_node_spy";
  const unboundEnabled = draft.enabled && !draft.account_id;
  const boundAccountLabel = draft.account_id
    ? (accounts.find((a) => a.id === draft.account_id)?.label ?? "Unbound")
    : "Unbound";
  const collapsedMeta = isSessionNode ? "SPY" : draft.universe;

  return (
    <Card className="border-border/60 bg-card/40">
      <CardHeader className="pb-3 pt-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-2 text-left"
              onClick={() => setExpanded((prev) => !prev)}
            >
              <ChevronDown
                className={`size-4 shrink-0 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
              />
              <span className="truncate text-sm font-semibold">{strategy.name}</span>
              {!expanded && dirty && (
                <span className="shrink-0 text-[11px] text-[var(--c-amber)]">• unsaved</span>
              )}
              <span className="truncate text-xs text-muted-foreground">
                {boundAccountLabel} · {collapsedMeta}
              </span>
            </button>
          </div>
          <TerminalToggle
            checked={draft.enabled}
            disabled={toggling}
            onCheckedChange={(checked) => void handleToggle(checked)}
            aria-label={`Enable ${strategy.name}`}
          />
        </div>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-4 pb-4">
          {doc && (
            <div className="space-y-2">
              <p className="text-xs font-medium">What this does</p>
              <div className="space-y-1.5">
                <p className="text-[11px] leading-relaxed">
                  <span className="font-semibold text-muted-foreground">
                    Trigger ·{" "}
                  </span>
                  {doc.trigger}
                </p>
                <p className="text-[11px] leading-relaxed">
                  <span className="font-semibold text-muted-foreground">
                    Action ·{" "}
                  </span>
                  {doc.action}
                </p>
                <p className="text-[11px] leading-relaxed">
                  <span className="font-semibold text-muted-foreground">
                    Exit ·{" "}
                  </span>
                  {doc.exit}
                </p>
              </div>
              <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
                <span
                  className={`inline-flex h-5 shrink-0 items-center rounded-full border px-1.5 text-[10px] font-medium ${statusToneClasses(doc.status.tone)}`}
                >
                  {doc.status.label}
                </span>
                <span className="text-[11px] leading-relaxed text-muted-foreground">
                  {doc.status.note}
                </span>
              </div>
            </div>
          )}

          {unboundEnabled && !isSessionNode && (
            <p className="text-xs text-[var(--c-amber)]">
              Enable has no effect until an account is bound — the scan skips
              unbound strategies.
            </p>
          )}

          <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
            <TerminalField label="Account">
              <Select
                value={draft.account_id || UNBOUND}
                onValueChange={(v) =>
                  updateDraft((d) => ({
                    ...d,
                    account_id: v === UNBOUND ? "" : (v ?? ""),
                  }))
                }
              >
                <SelectTrigger className="h-8 bg-[var(--bg-inset)] border-[var(--border)] font-mono text-[11px]">
                  <SelectValue placeholder="Select account" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={UNBOUND}>— Unbound —</SelectItem>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.label} · {a.broker}
                      {a.is_default ? " (default)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </TerminalField>

            <TerminalField label={isSessionNode ? "Symbol" : "Universe"}>
              {isSessionNode ? (
                <p className="text-sm text-muted-foreground">SPY</p>
              ) : (
                <Select
                  value={draft.universe}
                  onValueChange={(v) =>
                    updateDraft((d) => ({ ...d, universe: v ?? "sp500" }))
                  }
                >
                  <SelectTrigger className="h-8 bg-[var(--bg-inset)] border-[var(--border)] font-mono text-[11px]">
                    <SelectValue placeholder="Select universe" />
                  </SelectTrigger>
                    <SelectContent>
                      {/* Surface the stored value even if it's a legacy/active spec not
                          in the option list, so it never renders blank. */}
                      {!universes.some((u) => u.id === draft.universe) && (
                        <SelectItem value={draft.universe}>
                          {draft.universe === "active_watchlist"
                            ? "Active watchlist (legacy)"
                            : draft.universe}
                        </SelectItem>
                      )}
                      {universes.map((u) => (
                        <SelectItem key={u.id} value={u.id}>
                          {u.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
              )}
            </TerminalField>

            <TerminalField label="Notional ($)">
              <TerminalInput
                type="number"
                min={0}
                step={50}
                value={notionalText}
                onChange={(e) => {
                  const raw = e.target.value;
                  setNotionalText(raw);
                  updateDraft((d) => ({
                    ...d,
                    notional_usd: raw === "" ? 0 : Number(raw),
                  }));
                }}
              />
            </TerminalField>

            <TerminalField label="Entry mode">
              {isSessionNode ? (
                <p className="text-sm text-muted-foreground">
                  propose — entries are phone prompts
                </p>
              ) : (
                <Select
                  value={draft.entry_mode}
                  onValueChange={(v) =>
                    updateDraft((d) => ({
                      ...d,
                      entry_mode: (v ?? "propose") as "auto" | "propose",
                    }))
                  }
                >
                  <SelectTrigger className="h-8 bg-[var(--bg-inset)] border-[var(--border)] font-mono text-[11px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">auto</SelectItem>
                    <SelectItem value="propose">propose</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </TerminalField>

            <TerminalField label="Exit mode">
              {isSessionNode ? (
                <p className="text-sm text-muted-foreground">
                  propose — exits are phone prompts
                </p>
              ) : (
                <Select
                  value={draft.exit_mode}
                  onValueChange={(v) =>
                    updateDraft((d) => ({
                      ...d,
                      exit_mode: (v ?? "propose") as "auto" | "propose",
                    }))
                  }
                >
                  <SelectTrigger className="h-8 bg-[var(--bg-inset)] border-[var(--border)] font-mono text-[11px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">auto</SelectItem>
                    <SelectItem value="propose">propose</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </TerminalField>

            <TerminalField label="Max positions">
              <TerminalInput
                type="number"
                min={0}
                step={1}
                placeholder="No cap"
                value={draft.max_concurrent_positions ?? ""}
                onChange={(e) =>
                  updateDraft((d) => ({
                    ...d,
                    max_concurrent_positions:
                      e.target.value === ""
                        ? null
                        : Math.trunc(Number(e.target.value)),
                  }))
                }
              />
            </TerminalField>

            <TerminalField label="Max deployed ($)">
              <TerminalInput
                type="number"
                min={0}
                step={1000}
                placeholder="No cap"
                value={draft.max_deployed_usd ?? ""}
                onChange={(e) =>
                  updateDraft((d) => ({
                    ...d,
                    max_deployed_usd:
                      e.target.value === "" ? null : Number(e.target.value),
                  }))
                }
              />
            </TerminalField>

            {hasFarSupportMin && (
              <TerminalField label="Far support min (%)">
                <TerminalInput
                  type="number"
                  min={0}
                  step={0.5}
                  value={farSupportText}
                  onChange={(e) => {
                    const raw = e.target.value;
                    setFarSupportText(raw);
                    updateDraft((d) => ({
                      ...d,
                      params: {
                        ...d.params,
                        far_support_min: raw === "" ? 0 : Number(raw),
                      },
                    }));
                  }}
                />
              </TerminalField>
            )}
          </div>

          <p className="text-[11px] text-muted-foreground">
            What the strategy scans — independent of the watchlist you&apos;re
            viewing. Broad market keeps it faithful to the backtest; pin a
            specific watchlist to constrain it.
          </p>

          <p className="text-[11px] text-muted-foreground">
            Entry auto = the agent buys through the execution chokepoint when a
            signal fires (paper/dry-run only); propose = phone approval for each
            buy. Exit auto = same for sells when an exit rule triggers; propose =
            phone approval for each sell. You&apos;re notified either way.
          </p>

          <p className="text-[11px] text-muted-foreground">
            Caps total open + pending exposure in the bound account (blank = no
            cap). The strategy stops proposing once a cap is hit — your safety net
            on cluster days. For clean per-strategy budgets, give each strategy
            its own account.
          </p>

          {strategy.id === "dip_refined" && (
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              Refined dip adds a &apos;far from support&apos; filter (must be ≥
              X% above nearest support). In backtest this ~doubled recent
              excess over SPY (+1.6% → +3%/trade) but is NOT
              block-bootstrap-validated like the base dip — treat as a live
              forward-test, not a guarantee.
            </p>
          )}

          {isSessionNode && (
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              Shorts (resistance nodes) only fire when{" "}
              <strong>Allow short selling</strong> is on in Execution / Risk
              Limits — otherwise long-only (support nodes).
            </p>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex justify-end">
            <TerminalButton
              type="button"
              variant="primary"
              disabled={!dirty || saving}
              onClick={() => void handleSave()}
            >
              {saving && <Loader2 className="mr-2 size-3.5 animate-spin" />}
              Save
            </TerminalButton>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

type SettingsStrategiesPanelProps = {
  /** Hide intro copy when embedded in Automations (outer card provides the title). */
  embedded?: boolean;
};

export function SettingsStrategiesPanel({
  embedded = false,
}: SettingsStrategiesPanelProps) {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [accounts, setAccounts] = useState<StrategyAccount[]>([]);
  const [universes, setUniverses] = useState<StrategyUniverse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchStrategies(),
      fetchStrategyAccounts(),
      fetchStrategyUniverses(),
    ])
      .then(([strats, accts, unis]) => {
        if (!cancelled) {
          setStrategies(strats);
          setAccounts(accts);
          setUniverses(unis);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load strategies",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleSaved(updated: Strategy) {
    setStrategies((prev) =>
      prev.map((s) => (s.id === updated.id ? updated : s)),
    );
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (error && strategies.length === 0) {
    return <p className="text-sm text-destructive">{error}</p>;
  }

  return (
    <div className="space-y-5">
      {!embedded && (
        <div>
          <h3 className="text-sm font-medium">Strategies</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Enable automated scans, bind an execution account, and tune per-strategy
            sizing. Changes save per card.
          </p>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="space-y-4">
        {strategies.map((strategy) => (
          <StrategyCard
            key={`${strategy.id}-${strategy.updated_at}`}
            strategy={strategy}
            accounts={accounts}
            universes={universes}
            onSaved={handleSaved}
          />
        ))}
      </div>

      {strategies.length === 0 && (
        <p className="text-sm text-muted-foreground">No strategies configured.</p>
      )}
    </div>
  );
}
