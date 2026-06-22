"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  fetchStrategies,
  fetchStrategyAccounts,
  updateStrategy,
  type Strategy,
  type StrategyAccount,
} from "@/lib/strategies-api";

// Base UI Select reserves no special meaning for values, but the codebase
// convention (see performance-page) is a sentinel string for a "none" option
// rather than an empty string. "" is the real unbound value sent to the API.
const UNBOUND = "__unbound__";

type StrategyDraft = {
  enabled: boolean;
  account_id: string;
  notional_usd: number;
  exit_mode: "auto" | "propose";
  params: Record<string, unknown>;
};

type StrategyCardState = {
  draft: StrategyDraft;
  dirty: boolean;
  saving: boolean;
  error: string | null;
};

function strategyToDraft(strategy: Strategy): StrategyDraft {
  return {
    enabled: strategy.enabled,
    account_id: strategy.account_id ?? "",
    notional_usd: strategy.notional_usd,
    exit_mode: strategy.exit_mode,
    params: { ...strategy.params },
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
  if (draft.exit_mode !== original.exit_mode) patch.exit_mode = draft.exit_mode;

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
  onSaved,
}: {
  strategy: Strategy;
  accounts: StrategyAccount[];
  onSaved: (updated: Strategy) => void;
}) {
  const original = strategyToDraft(strategy);
  const hasFarSupportMin = "far_support_min" in strategy.params;

  const [state, setState] = useState<StrategyCardState>(() => ({
    draft: original,
    dirty: false,
    saving: false,
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

  const { draft, dirty, saving, error } = state;
  const unboundEnabled = draft.enabled && !draft.account_id;

  return (
    <Card className="border-border/60 bg-card/40">
      <CardHeader className="pb-3 pt-4">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-sm font-semibold">{strategy.name}</CardTitle>
          <Switch
            checked={draft.enabled}
            onCheckedChange={(checked) =>
              updateDraft((d) => ({ ...d, enabled: checked }))
            }
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4 pb-4">
        {unboundEnabled && (
          <p className="text-xs text-amber-500">
            Enable has no effect until an account is bound — the scan skips
            unbound strategies.
          </p>
        )}

        <div className="space-y-1.5">
          <Label className="text-xs">Account</Label>
          <Select
            value={draft.account_id || UNBOUND}
            onValueChange={(v) =>
              updateDraft((d) => ({
                ...d,
                account_id: v === UNBOUND ? "" : (v ?? ""),
              }))
            }
          >
            <SelectTrigger className="h-8">
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
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">Notional ($)</Label>
          <Input
            type="number"
            min={0}
            step={50}
            className="h-8"
            value={draft.notional_usd}
            onChange={(e) =>
              updateDraft((d) => ({
                ...d,
                notional_usd: Number(e.target.value),
              }))
            }
          />
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">Exit mode</Label>
          <Select
            value={draft.exit_mode}
            onValueChange={(v) =>
              updateDraft((d) => ({
                ...d,
                exit_mode: (v ?? "propose") as "auto" | "propose",
              }))
            }
          >
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">auto</SelectItem>
              <SelectItem value="propose">propose</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[11px] text-muted-foreground">
            Exits are phone-approved entries only for now; automated exits arrive
            with the exit module.
          </p>
        </div>

        {hasFarSupportMin && (
          <div className="space-y-1.5">
            <Label className="text-xs">Far support min (%)</Label>
            <Input
              type="number"
              min={0}
              step={0.5}
              className="h-8"
              value={Number(draft.params.far_support_min)}
              onChange={(e) =>
                updateDraft((d) => ({
                  ...d,
                  params: {
                    ...d.params,
                    far_support_min: Number(e.target.value),
                  },
                }))
              }
            />
          </div>
        )}

        {strategy.id === "dip_refined" && (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Refined dip adds a &apos;far from support&apos; filter (must be ≥
            X% above nearest support). In backtest this ~doubled recent excess
            over SPY (+1.6% → +3%/trade) but is NOT block-bootstrap-validated
            like the base dip — treat as a live forward-test, not a guarantee.
          </p>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        <div className="flex justify-end">
          <Button
            type="button"
            size="sm"
            disabled={!dirty || saving}
            onClick={() => void handleSave()}
          >
            {saving && <Loader2 className="mr-2 size-3.5 animate-spin" />}
            Save
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function SettingsStrategiesPanel() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [accounts, setAccounts] = useState<StrategyAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchStrategies(), fetchStrategyAccounts()])
      .then(([strats, accts]) => {
        if (!cancelled) {
          setStrategies(strats);
          setAccounts(accts);
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
      <div>
        <h3 className="text-sm font-medium">Strategies</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Enable automated scans, bind an execution account, and tune per-strategy
          sizing. Changes save per card.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="space-y-4">
        {strategies.map((strategy) => (
          <StrategyCard
            key={`${strategy.id}-${strategy.updated_at}`}
            strategy={strategy}
            accounts={accounts}
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
