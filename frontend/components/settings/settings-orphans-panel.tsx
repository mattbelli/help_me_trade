"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { TerminalButton } from "@/components/ui/terminal";
import { Card, CardContent } from "@/components/ui/card";
import {
  fetchFillOrphans,
  repairFillOrphan,
  type FillOrphan,
} from "@/lib/fill-orphans-api";

// App-wide 12-hour ET timestamp, e.g. "Jul 12 · 2:48p".
function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
  });
  const time = d
    .toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    })
    .replace(" AM", "a")
    .replace(" PM", "p");
  return `${date} · ${time}`;
}

function statusClass(status: FillOrphan["status"]): string {
  if (status === "repaired") return "text-[var(--c-green)]";
  return "text-[var(--c-amber)]"; // open / notified both still need attention
}

function OrphanRow({
  orphan,
  onRepaired,
}: {
  orphan: FillOrphan;
  onRepaired: (id: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canRepair = orphan.status !== "repaired";

  async function handleRepair() {
    setBusy(true);
    setError(null);
    try {
      await repairFillOrphan(orphan.id);
      onRepaired(orphan.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to mark repaired");
      setBusy(false);
    }
  }

  return (
    <Card className="border-border/60 bg-card/40">
      <CardContent className="flex items-start justify-between gap-3 py-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">
              {orphan.symbol ?? "—"}
            </span>
            <span
              className={`font-mono text-[11px] lowercase ${statusClass(orphan.status)}`}
            >
              {orphan.status}
            </span>
            <span className="text-[11px] text-muted-foreground">
              {formatWhen(orphan.created_at)}
            </span>
          </div>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {orphan.reason}
            {orphan.detail ? ` — ${orphan.detail}` : ""}
          </p>
          <p className="font-mono text-[10px] text-muted-foreground/70">
            {orphan.strategy_id ? `strat ${orphan.strategy_id} · ` : ""}
            {orphan.execution_id ? `exec ${orphan.execution_id}` : "no execution id"}
          </p>
          {error && <p className="text-[11px] text-destructive">{error}</p>}
        </div>
        {canRepair && (
          <TerminalButton
            type="button"
            variant="primary"
            disabled={busy}
            onClick={() => void handleRepair()}
          >
            {busy && <Loader2 className="mr-2 size-3.5 animate-spin" />}
            Mark repaired
          </TerminalButton>
        )}
      </CardContent>
    </Card>
  );
}

export function SettingsOrphansPanel() {
  const [orphans, setOrphans] = useState<FillOrphan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await fetchFillOrphans();
      setOrphans(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load orphans");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function handleRepaired(id: string) {
    setOrphans((prev) =>
      prev.map((o) => (o.id === id ? { ...o, status: "repaired" } : o)),
    );
  }

  const unresolved = orphans.filter((o) => o.status !== "repaired").length;

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-sm font-medium">Unmanaged fills</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          A strategy BUY that filled but couldn&apos;t be attributed to a managed
          position — the shares are held with{" "}
          <strong>no stop-loss or exit rules attached</strong>. Attach management
          by hand (adjust the position / arm an exit), then mark it repaired to
          clear the page. Bookkeeping only — this never places or cancels a
          broker order.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : orphans.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No unmanaged fills — every strategy fill is accounted for.
        </p>
      ) : (
        <>
          {unresolved === 0 && (
            <p className="text-xs text-[var(--c-green)]">
              All clear — nothing needs repair.
            </p>
          )}
          <div className="space-y-3">
            {orphans.map((orphan) => (
              <OrphanRow
                key={orphan.id}
                orphan={orphan}
                onRepaired={handleRepaired}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
