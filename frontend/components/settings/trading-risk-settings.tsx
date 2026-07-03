"use client";

import { ShieldAlertIcon } from "lucide-react";
import { useEffect, useState } from "react";

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
import { setKillSwitch, type TradingSettings } from "@/lib/api";

const ADMIN_TOKEN_SESSION_KEY = "hmt-admin-token";

const DEFAULT_TRADING: TradingSettings = {
  execution_mode: "dry_run",
  kill_switch_active: false,
  max_order_value_usd: 50000,
  max_position_pct: 30,
  max_open_orders: 10,
  max_daily_orders: 20,
  max_daily_loss_usd: 2500,
  max_daily_notional_usd: 150000,
  max_rebalance_run_notional_usd: 120000,
  allow_short_selling: false,
};

type Props = {
  value: TradingSettings | undefined;
  onChange: (trading: TradingSettings) => void;
  adminToken: string;
  onAdminTokenChange: (token: string) => void;
};

export function TradingRiskSettings({
  value,
  onChange,
  adminToken,
  onAdminTokenChange,
}: Props) {
  const trading = value ?? DEFAULT_TRADING;
  const [killSwitchConfirmOpen, setKillSwitchConfirmOpen] = useState(false);
  const [pendingKillSwitch, setPendingKillSwitch] = useState<boolean | null>(
    null,
  );
  const [disarmTokenOpen, setDisarmTokenOpen] = useState(false);
  const [disarmToken, setDisarmToken] = useState("");
  const [killSwitchBusy, setKillSwitchBusy] = useState(false);
  const [killSwitchError, setKillSwitchError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = sessionStorage.getItem(ADMIN_TOKEN_SESSION_KEY);
    if (stored && !adminToken) {
      onAdminTokenChange(stored);
    }
  }, [adminToken, onAdminTokenChange]);

  function persistAdminToken(token: string) {
    onAdminTokenChange(token);
    if (typeof window === "undefined") return;
    if (token) {
      sessionStorage.setItem(ADMIN_TOKEN_SESSION_KEY, token);
    } else {
      sessionStorage.removeItem(ADMIN_TOKEN_SESSION_KEY);
    }
  }

  function patch(partial: Partial<TradingSettings>) {
    onChange({ ...trading, ...partial });
  }

  function requestKillSwitchToggle(checked: boolean) {
    setKillSwitchError(null);
    if (checked) {
      setPendingKillSwitch(true);
      setKillSwitchConfirmOpen(true);
      return;
    }
    setDisarmToken(adminToken);
    setDisarmTokenOpen(true);
  }

  async function confirmKillSwitchArm() {
    setKillSwitchBusy(true);
    setKillSwitchError(null);
    try {
      const result = await setKillSwitch(true);
      patch({ kill_switch_active: result.kill_switch_active });
      setKillSwitchConfirmOpen(false);
      setPendingKillSwitch(null);
    } catch (err) {
      setKillSwitchError(
        err instanceof Error ? err.message : "Failed to arm kill switch",
      );
    } finally {
      setKillSwitchBusy(false);
    }
  }

  async function confirmKillSwitchDisarm() {
    const token = disarmToken.trim();
    if (!token) {
      setKillSwitchError("Admin token required to disarm kill switch");
      return;
    }
    setKillSwitchBusy(true);
    setKillSwitchError(null);
    try {
      const result = await setKillSwitch(false, token);
      patch({ kill_switch_active: result.kill_switch_active });
      persistAdminToken(token);
      setDisarmTokenOpen(false);
      setDisarmToken("");
    } catch (err) {
      setKillSwitchError(
        err instanceof Error ? err.message : "Failed to disarm kill switch",
      );
    } finally {
      setKillSwitchBusy(false);
    }
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
                Arming uses your normal API token; disarming requires the admin
                token.
              </p>
            </div>
          </div>
          <TerminalToggle
            tone="danger"
            checked={trading.kill_switch_active}
            onCheckedChange={requestKillSwitchToggle}
            disabled={killSwitchBusy}
            aria-label="Kill switch"
          />
        </div>
        {killSwitchError && (
          <p className="mt-2 font-mono text-[10px] text-[var(--c-red)]">
            {killSwitchError}
          </p>
        )}
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
        <TerminalField label="Max daily loss (USD)" htmlFor="max-daily-loss">
          <TerminalInput
            id="max-daily-loss"
            type="number"
            min={0}
            step={100}
            value={trading.max_daily_loss_usd}
            onChange={(e) =>
              patch({ max_daily_loss_usd: Number(e.target.value) || 0 })
            }
          />
        </TerminalField>
        <TerminalField
          label="Max daily notional (USD)"
          htmlFor="max-daily-notional"
        >
          <TerminalInput
            id="max-daily-notional"
            type="number"
            min={0}
            step={1000}
            value={trading.max_daily_notional_usd}
            onChange={(e) =>
              patch({ max_daily_notional_usd: Number(e.target.value) || 0 })
            }
          />
        </TerminalField>
        <TerminalField
          label="Max rebalance run notional (USD)"
          htmlFor="max-rebalance-run-notional"
        >
          <TerminalInput
            id="max-rebalance-run-notional"
            type="number"
            min={0}
            step={1000}
            value={trading.max_rebalance_run_notional_usd}
            onChange={(e) =>
              patch({
                max_rebalance_run_notional_usd: Number(e.target.value) || 0,
              })
            }
          />
        </TerminalField>
      </div>

      <TerminalField
        label="Admin token (risk-control changes)"
        htmlFor="admin-token"
        hint="Required to save execution/risk limits. Stored in sessionStorage only for this browser tab session."
      >
        <TerminalInput
          id="admin-token"
          type="password"
          autoComplete="off"
          value={adminToken}
          placeholder="API_ADMIN_TOKEN value"
          onChange={(e) => persistAdminToken(e.target.value)}
        />
      </TerminalField>

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
              disabled={killSwitchBusy}
            >
              Cancel
            </TerminalButton>
            <TerminalButton
              type="button"
              variant="danger"
              onClick={confirmKillSwitchArm}
              disabled={killSwitchBusy}
            >
              {killSwitchBusy ? "Arming…" : "Enable kill switch"}
            </TerminalButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={disarmTokenOpen} onOpenChange={setDisarmTokenOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Disarm kill switch?</DialogTitle>
            <DialogDescription>
              Disarming requires the admin token. Order submissions may resume per
              current risk limits.
            </DialogDescription>
          </DialogHeader>
          <TerminalField label="Admin token" htmlFor="disarm-admin-token">
            <TerminalInput
              id="disarm-admin-token"
              type="password"
              autoComplete="off"
              value={disarmToken}
              placeholder="API_ADMIN_TOKEN value"
              onChange={(e) => setDisarmToken(e.target.value)}
            />
          </TerminalField>
          <DialogFooter className="gap-2 sm:gap-0">
            <TerminalButton
              type="button"
              onClick={() => {
                setDisarmTokenOpen(false);
                setDisarmToken("");
              }}
              disabled={killSwitchBusy}
            >
              Cancel
            </TerminalButton>
            <TerminalButton
              type="button"
              variant="danger"
              onClick={confirmKillSwitchDisarm}
              disabled={killSwitchBusy}
            >
              {killSwitchBusy ? "Disarming…" : "Disarm kill switch"}
            </TerminalButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export { DEFAULT_TRADING };
