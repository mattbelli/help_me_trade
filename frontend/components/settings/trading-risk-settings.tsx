"use client";

import { ShieldAlertIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
        <h3 className="text-sm font-semibold">Execution / Risk Limits</h3>
        <p className="text-xs text-muted-foreground">
          Controls whether approved orders are logged only (dry run) or sent to
          Alpaca paper. Live trading is not available yet.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="execution-mode">Execution mode</Label>
        <select
          id="execution-mode"
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
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
        </select>
        {trading.execution_mode === "live" && (
          <p className="text-xs text-[var(--c-amber)]">
            Live execution is disabled. Switch to dry run or paper.
          </p>
        )}
      </div>

      <div
        className={`rounded-lg border p-3 ${
          trading.kill_switch_active
            ? "border-destructive/50 bg-destructive/5"
            : "border-border"
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-start gap-2">
            <ShieldAlertIcon
              className={`mt-0.5 size-4 shrink-0 ${
                trading.kill_switch_active
                  ? "text-destructive"
                  : "text-muted-foreground"
              }`}
            />
            <div>
              <p className="text-sm font-medium">Kill switch</p>
              <p className="text-xs text-muted-foreground">
                When active, all order submissions are rejected immediately.
              </p>
            </div>
          </div>
          <Switch
            checked={trading.kill_switch_active}
            onCheckedChange={requestKillSwitchToggle}
            aria-label="Kill switch"
          />
        </div>
      </div>

      <div
        className={`rounded-lg border p-3 ${
          trading.allow_short_selling
            ? "border-[var(--c-amber)]/50 bg-[var(--c-amber)]/5"
            : "border-border"
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-start gap-2">
            <ShieldAlertIcon
              className={`mt-0.5 size-4 shrink-0 ${
                trading.allow_short_selling
                  ? "text-[var(--c-amber)]"
                  : "text-muted-foreground"
              }`}
            />
            <div>
              <p className="text-sm font-medium">Allow short selling</p>
              <p className="text-xs text-muted-foreground">
                Lets strategies sell-to-open (short) when flat. Paper only — live
                short selling stays blocked. Shorts get the same value/position
                caps as buys.
              </p>
            </div>
          </div>
          <Switch
            checked={trading.allow_short_selling}
            onCheckedChange={(checked) =>
              patch({ allow_short_selling: checked })
            }
            aria-label="Allow short selling"
          />
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="max-order-value">Max order value (USD)</Label>
          <Input
            id="max-order-value"
            type="number"
            min={0}
            step={100}
            value={trading.max_order_value_usd}
            onChange={(e) =>
              patch({ max_order_value_usd: Number(e.target.value) || 0 })
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="max-position-pct">Max position (% of portfolio)</Label>
          <Input
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
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="max-open-orders">Max open orders</Label>
          <Input
            id="max-open-orders"
            type="number"
            min={0}
            step={1}
            value={trading.max_open_orders}
            onChange={(e) =>
              patch({ max_open_orders: Number(e.target.value) || 0 })
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="max-daily-orders">Max daily orders</Label>
          <Input
            id="max-daily-orders"
            type="number"
            min={0}
            step={1}
            value={trading.max_daily_orders}
            onChange={(e) =>
              patch({ max_daily_orders: Number(e.target.value) || 0 })
            }
          />
        </div>
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
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setKillSwitchConfirmOpen(false);
                setPendingKillSwitch(null);
              }}
            >
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={confirmKillSwitch}>
              Enable kill switch
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export { DEFAULT_TRADING };
