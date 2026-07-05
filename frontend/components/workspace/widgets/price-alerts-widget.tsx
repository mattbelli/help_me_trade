"use client";

import { Pencil, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import {
  WidgetEmpty,
  WidgetError,
  WidgetLoading,
} from "@/components/workspace/widget-states";
import {
  createPriceAlert,
  deletePriceAlert,
  fetchPriceAlerts,
  formatMoney,
  updatePriceAlert,
  type PriceAlert,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ChannelId } from "@/lib/workspace/channels";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import type { WidgetProps } from "@/lib/workspace/widget-registry";

const POLL_MS = 20_000;

const DIRECTIONS = ["above", "below", "cross"] as const;
type AlertDirection = (typeof DIRECTIONS)[number];

type AlertFormState = {
  editingId: number | null;
  symbol: string;
  price: string;
  direction: AlertDirection;
  repeat: boolean;
  note: string;
};

const EMPTY_FORM: AlertFormState = {
  editingId: null,
  symbol: "",
  price: "",
  direction: "cross",
  repeat: false,
  note: "",
};

function alertToForm(alert: PriceAlert): AlertFormState {
  return {
    editingId: alert.id,
    symbol: alert.symbol,
    price: String(alert.price),
    direction: (DIRECTIONS.includes(alert.direction as AlertDirection)
      ? alert.direction
      : "cross") as AlertDirection,
    repeat: Boolean(alert.repeat),
    note: alert.note ?? "",
  };
}

export function PriceAlertsWidget({ config, onConfigChange }: WidgetProps) {
  const channel: ChannelId = config.channel ?? "none";
  const { publish, linked } = usePublishSymbol(channel);
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editFormOpen, setEditFormOpen] = useState(false);
  const [form, setForm] = useState<AlertFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const createFormOpen = config.adding ?? false;
  const formOpen = createFormOpen || editFormOpen;

  const load = useCallback(async () => {
    try {
      const data = await fetchPriceAlerts();
      setAlerts(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load alerts");
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (createFormOpen) {
      setForm(EMPTY_FORM);
      setFormError(null);
    }
  }, [createFormOpen]);

  function openEditForm(alert: PriceAlert) {
    setForm(alertToForm(alert));
    setFormError(null);
    setEditFormOpen(true);
  }

  function closeForm() {
    if (editFormOpen) {
      setEditFormOpen(false);
      setForm(EMPTY_FORM);
      setFormError(null);
    }
    if (createFormOpen) {
      onConfigChange({ adding: false });
    }
  }

  async function handleSave() {
    const symbol = form.symbol.trim().toUpperCase();
    const price = Number(form.price.trim());

    if (!symbol) {
      setFormError("Symbol is required");
      return;
    }
    if (!Number.isFinite(price)) {
      setFormError("Enter a valid price");
      return;
    }

    setFormError(null);
    setSaving(true);
    try {
      if (form.editingId != null) {
        await updatePriceAlert(form.editingId, {
          price,
          direction: form.direction,
          repeat: form.repeat,
          note: form.note.trim() || undefined,
        });
      } else {
        await createPriceAlert({
          symbol,
          price,
          direction: form.direction,
          repeat: form.repeat,
          note: form.note.trim() || undefined,
        });
      }
      closeForm();
      await load();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not save alert",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    try {
      await deletePriceAlert(id);
      if (form.editingId === id) closeForm();
      await load();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not delete alert",
      );
    }
  }

  return (
    <div className="flex h-full flex-col">
      {formOpen ? (
        <div className="shrink-0 space-y-2 border-b border-[var(--border)] px-2 py-2 font-mono text-[length:var(--fs-body)]">
          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-0.5">
              <span className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                Symbol
              </span>
              <input
                type="text"
                value={form.symbol}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    symbol: e.target.value.toUpperCase(),
                  }))
                }
                disabled={form.editingId != null}
                placeholder="AAPL"
                className="w-full rounded border border-[var(--border)] bg-transparent px-1.5 py-1 text-[length:var(--fs-body)] text-foreground outline-none focus:border-[var(--c-accent)] disabled:cursor-not-allowed disabled:opacity-60"
              />
            </label>
            <label className="space-y-0.5">
              <span className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
                Price
              </span>
              <input
                type="number"
                step="any"
                value={form.price}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, price: e.target.value }))
                }
                placeholder="0.00"
                className="w-full rounded border border-[var(--border)] bg-transparent px-1.5 py-1 text-[length:var(--fs-body)] tabular-nums text-foreground outline-none focus:border-[var(--c-accent)]"
              />
            </label>
          </div>

          <div className="space-y-0.5">
            <span className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
              Direction
            </span>
            <div className="flex gap-1">
              {DIRECTIONS.map((dir) => (
                <button
                  key={dir}
                  type="button"
                  onClick={() =>
                    setForm((prev) => ({ ...prev, direction: dir }))
                  }
                  className={cn(
                    "flex-1 rounded border px-1.5 py-0.5 text-[length:var(--fs-table)] tracking-[0.08em] uppercase transition-colors",
                    form.direction === dir
                      ? "border-[var(--c-accent)] bg-[color-mix(in_srgb,var(--c-accent)_12%,transparent)] text-[var(--c-accent)]"
                      : "border-[var(--border)] text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]",
                  )}
                >
                  {dir}
                </button>
              ))}
            </div>
          </div>

          <label className="flex items-center gap-1.5 text-[length:var(--fs-table)] text-[var(--fg-dim)]">
            <input
              type="checkbox"
              checked={form.repeat}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, repeat: e.target.checked }))
              }
              className="size-3 rounded border-[var(--border)] accent-[var(--c-accent)]"
            />
            Persistent (re-arms after each crossing)
          </label>

          <label className="block space-y-0.5">
            <span className="text-[length:var(--fs-table)] tracking-[0.08em] text-[var(--fg-faint)] uppercase">
              Note
            </span>
            <input
              type="text"
              value={form.note}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, note: e.target.value }))
              }
              placeholder="Optional"
              className="w-full rounded border border-[var(--border)] bg-transparent px-1.5 py-1 text-[length:var(--fs-body)] text-foreground outline-none focus:border-[var(--c-accent)]"
            />
          </label>

          {formError ? (
            <p className="text-[length:var(--fs-table)] text-[var(--c-red)]">{formError}</p>
          ) : null}

          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="rounded border border-[var(--c-accent)] px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--c-accent)] transition-colors hover:bg-[color-mix(in_srgb,var(--c-accent)_12%,transparent)] disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={closeForm}
              disabled={saving}
              className="rounded border border-[var(--border)] px-2 py-0.5 text-[length:var(--fs-table)] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-auto px-1.5 py-1 scroll-shadow-y">
        {loading && alerts.length === 0 ? (
          <WidgetLoading cols={[2, 1, 1]} />
        ) : error && alerts.length === 0 ? (
          <WidgetError message={error} onRetry={load} className="p-0" />
        ) : alerts.length === 0 ? (
          <WidgetEmpty message="No price alerts configured" className="p-0" />
        ) : (
          <table className="w-full border-collapse font-mono text-[length:var(--fs-title)]">
            <thead>
              <tr>
                {(["Sym", "Price", "Dir", "Status", "Note", ""] as const).map(
                  (h, idx) => (
                    <th
                      key={h || "actions"}
                      className={cn(
                        "border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-micro)] font-medium tracking-[0.08em] text-[var(--fg-table-header)] uppercase",
                        idx === 0 ||
                          h === "Dir" ||
                          h === "Status" ||
                          h === "Note"
                          ? "text-left"
                          : "text-right",
                        h === "" && "w-14",
                      )}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <tr
                  key={alert.id}
                  className="group group/row transition-colors hover:bg-[var(--bg-hover)]"
                >
                  <td className="border-b border-[var(--border)] px-2 py-1.5 font-medium text-[var(--c-accent)]">
                    <ClickableSymbol
                      symbol={alert.symbol}
                      linked={linked}
                      onActivate={() => publish(alert.symbol)}
                    />
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-right tabular-nums text-foreground">
                    {formatMoney(alert.price)}
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-body)] uppercase text-[var(--fg-dim)]">
                    {alert.direction}
                    {alert.repeat ? (
                      <span
                        className="ml-1 text-[var(--c-accent)]"
                        title="Persistent — re-arms after each crossing"
                      >
                        ↻
                      </span>
                    ) : null}
                  </td>
                  <td
                    className={cn(
                      "border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-body)] uppercase",
                      alert.triggered && !alert.repeat
                        ? "text-[var(--c-green)]"
                        : "text-[var(--c-blue)]",
                    )}
                  >
                    {alert.repeat
                      ? "persistent"
                      : alert.triggered
                        ? "triggered"
                        : "active"}
                  </td>
                  <td className="border-b border-[var(--border)] px-2 py-1.5 text-[length:var(--fs-body)] text-[var(--fg-faint)]">
                    {alert.note || "—"}
                  </td>
                  <td className="border-b border-[var(--border)] px-1 py-1.5">
                    <div className="flex justify-end gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
                      <button
                        type="button"
                        onClick={() => openEditForm(alert)}
                        title="Edit alert"
                        className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--c-accent)]"
                      >
                        <Pencil className="size-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(alert.id)}
                        title="Delete alert"
                        className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
