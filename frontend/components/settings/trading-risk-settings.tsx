"use client";

import { ShieldAlertIcon } from "lucide-react";
import { useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  SettingsSection,
  TerminalButton,
  TerminalField,
  TerminalInput,
  TerminalSelect,
  TerminalToggle,
} from "@/components/ui/terminal";
import type { TradingSettings } from "@/lib/api";

const DEFAULT_TRADING: TradingSettings = {
  execution_mode: "dry_run",
  kill_switch_active: false,
  max_order_value_usd: 50000,
  max_position_pct: 30,
  max_open_orders: 10,
  max_daily_orders: 20,
  allow_short_selling: false,
};

type Props = {
  value: TradingSettings | undefined;
  onChange: (trading: TradingSettings) => void;
};

export function TradingRiskSettings({ value, onChange }: Props) {
  const trading = value ?? DEFAULT_TRADING;
  const [killSwitchConfirmOpen, setKillSwitchConfirmOpen] = useState(false);
  const [pendingKillSwitch, setPendingKillSwitch] = useState<boolean | null>(
    null,
  );

  function patch(partial: Partial<TradingSettings>) {
    onChange({ ...trading, ...partial });
  }

  function requestKillSwitchToggle(checked: boolean) {
    if (checked) {
      setPendingKillSwitch(true);
      setKillSwitchConfirmOpen(true);
      return;
    }
    patch({ kill_switch_active: false });
  }

  function confirmKillSwitch() {
    if (pendingKillSwitch) {
      patch({ kill_switch_active: true });
    }
    setKillSwitchConfirmOpen(false);
    setPendingKillSwitch(null);
  }

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <h3 className="font-mono text-[11px] tracking-[0.1em] uppercase text-foreground">
          Execution / Risk Limits
        </h3>
        <p className="font-mono text-[10px] leading-snug text-[var(--fg-dim)]">
          Controls whether approved orders are logged only (dry run) or sent to
          Alpaca paper. Live trading is not available yet.
        </p>
      </div>

      <TerminalField label="Execution mode" htmlFor="execution-mode">
        <TerminalSelect
          id="execution-mode"
          value={trading.execution_mode}
          onChange={(e) =>
            patch({
              execution_mode: e.target.value as TradingSettings["execution_mode"],
            })
          }
        >
          <option value="dry_run">Dry run — log only, no broker POST</option>
          <option value="paper">Paper — submit to Alpaca paper account</option>
          <option value="live" disabled>
            Live — coming soon (Increment 3)
          </option>
        </TerminalSelect>
        {trading.execution_mode === "live" && (
          <p className="font-mono text-[10px] text-[var(--c-amber)]">
            Live execution is disabled. Switch to dry run or paper.
          </p>
        )}
      </TerminalField>

      <SettingsSection
        variant="card"
        tone={trading.kill_switch_active ? "danger" : "default"}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-start gap-2">
            <ShieldAlertIcon
              className={`mt-0.5 size-4 shrink-0 ${
                trading.kill_switch_active
                  ? "text-[var(--c-red)]"
                  : "text-[var(--fg-faint)]"
              }`}
            />
            <div>
              <p className="font-mono text-[11px] text-foreground">Kill switch</p>
              <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                When active, all order submissions are rejected immediately.
              </p>
            </div>
          </div>
          <TerminalToggle
            tone="danger"
            checked={trading.kill_switch_active}
            onCheckedChange={requestKillSwitchToggle}
            aria-label="Kill switch"
          />
        </div>
      </SettingsSection>

      <SettingsSection
        variant="card"
        tone={trading.allow_short_selling ? "warn" : "default"}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-start gap-2">
            <ShieldAlertIcon
              className={`mt-0.5 size-4 shrink-0 ${
                trading.allow_short_selling
                  ? "text-[var(--c-amber)]"
                  : "text-[var(--fg-faint)]"
              }`}
            />
            <div>
              <p className="font-mono text-[11px] text-foreground">
                Allow short selling
              </p>
              <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                Lets strategies sell-to-open (short) when flat. Paper only — live
                short selling stays blocked. Shorts get the same value/position
                caps as buys.
              </p>
            </div>
          </div>
          <TerminalToggle
            tone="warn"
            checked={trading.allow_short_selling}
            onCheckedChange={(checked) =>
              patch({ allow_short_selling: checked })
            }
            aria-label="Allow short selling"
          />
        </div>
      </SettingsSection>

      <div className="grid gap-3 sm:grid-cols-2">
        <TerminalField label="Max order value (USD)" htmlFor="max-order-value">
          <TerminalInput
            id="max-order-value"
            type="number"
            min={0}
            step={100}
            value={trading.max_order_value_usd}
            onChange={(e) =>
              patch({ max_order_value_usd: Number(e.target.value) || 0 })
            }
          />
        </TerminalField>
        <TerminalField
          label="Max position (% of portfolio)"
          htmlFor="max-position-pct"
        >
          <TerminalInput
            id="max-position-pct"
            type="number"
            min={0}
            max={100}
            step={1}
            value={trading.max_position_pct}
            onChange={(e) =>
              patch({ max_position_pct: Number(e.target.value) || 0 })
            }
          />
        </TerminalField>
        <TerminalField label="Max open orders" htmlFor="max-open-orders">
          <TerminalInput
            id="max-open-orders"
            type="number"
            min={0}
            step={1}
            value={trading.max_open_orders}
            onChange={(e) =>
              patch({ max_open_orders: Number(e.target.value) || 0 })
            }
          />
        </TerminalField>
        <TerminalField label="Max daily orders" htmlFor="max-daily-orders">
          <TerminalInput
            id="max-daily-orders"
            type="number"
            min={0}
            step={1}
            value={trading.max_daily_orders}
            onChange={(e) =>
              patch({ max_daily_orders: Number(e.target.value) || 0 })
            }
          />
        </TerminalField>
      </div>

      <Dialog open={killSwitchConfirmOpen} onOpenChange={setKillSwitchConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Enable kill switch?</DialogTitle>
            <DialogDescription>
              This will block all new order submissions until you turn the kill
              switch off. Existing broker orders are not cancelled automatically.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 sm:gap-0">
            <TerminalButton
              type="button"
              onClick={() => {
                setKillSwitchConfirmOpen(false);
                setPendingKillSwitch(null);
              }}
            >
              Cancel
            </TerminalButton>
            <TerminalButton type="button" variant="danger" onClick={confirmKillSwitch}>
              Enable kill switch
            </TerminalButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export { DEFAULT_TRADING };
