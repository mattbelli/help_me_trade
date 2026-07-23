"use client";

// LIVE — sub-nav (mechanical | llm | human) → per-family roster tables →
// drill-in actor pages. mechanical rows come from /api/strategies; llm/human
// rows are the paper-book accounts (family from the v1 hardcoded map).

import { useEffect, useState } from "react";

import {
  fetchLabAccounts,
  fetchLabDayPnl,
  fetchLabStrategies,
  type LabAccount,
  type LabStrategy,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import { ActorPage } from "./actor-page";
import {
  familyForAccount,
  HUMAN_BOOK_ACCOUNTS,
  LLM_BOOK_ACCOUNTS,
  type ActorFamily,
  type LabRoute,
} from "./lab-nav";
import {
  EmptyNote,
  ErrorNote,
  Frame,
  LoadingNote,
  money,
  Tag,
  TD,
  TD_NUM,
  TH,
  TH_NUM,
  tone,
} from "./lab-ui";

const FAMILIES: ActorFamily[] = ["mechanical", "llm", "human"];
const FAMILY_NOTE: Record<ActorFamily, string> = {
  mechanical: "deterministic rules · fixed universe",
  llm: "each on its own paper account",
  human: "human-directed · you decide, AI implements",
};

export function LiveTab({
  route,
  onRoute,
}: {
  route: LabRoute;
  onRoute: (r: LabRoute) => void;
}) {
  return (
    <div className="space-y-4">
      {/* actor family sub-nav (plain lowercase colored words) */}
      <div className="flex items-center gap-5 border-b border-[var(--line-divider)] pb-2">
        {FAMILIES.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => onRoute({ tab: "live", family: f, actor: null })}
            className={cn(
              "text-[length:var(--fs-body)] lowercase outline-none",
              route.family === f && !route.actor
                ? f === "mechanical"
                  ? "text-[var(--fg-dim)] underline decoration-[var(--c-accent)] decoration-2 underline-offset-[6px]"
                  : "text-[var(--c-violet)] underline decoration-[var(--c-accent)] decoration-2 underline-offset-[6px]"
                : "text-[var(--fg-faint)] hover:text-[var(--fg-dim)]",
            )}
          >
            {f}
          </button>
        ))}
        <span className="ml-auto text-[length:var(--fs-table)] text-[var(--fg-faint)]">
          {FAMILY_NOTE[route.family]}
        </span>
      </div>

      {route.actor ? (
        <ActorPage
          family={route.family}
          actorId={route.actor}
          onBack={() =>
            onRoute({ tab: "live", family: route.family, actor: null })
          }
        />
      ) : route.family === "mechanical" ? (
        <MechanicalRoster onRoute={onRoute} />
      ) : (
        <BookRoster family={route.family} onRoute={onRoute} />
      )}
    </div>
  );
}

function MechanicalRoster({ onRoute }: { onRoute: (r: LabRoute) => void }) {
  const [rows, setRows] = useState<LabStrategy[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchLabStrategies()
      .then((r) => !cancelled && setRows(r))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "load failed"));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!rows) return <LoadingNote label="loading strategies…" />;
  if (rows.length === 0) return <EmptyNote>no strategies registered</EmptyNote>;

  return (
    <Frame>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH}>strategy</th>
            <th className={TH}>state</th>
            <th className={TH}>account</th>
            <th className={TH_NUM}>max concurrent</th>
            <th className={TH_NUM}>max deployed</th>
            <th className={TH}>universe</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr
              key={s.id}
              className="cursor-pointer hover:bg-[var(--surface-hover)]"
              onClick={() =>
                onRoute({ tab: "live", family: "mechanical", actor: s.id })
              }
            >
              <td className={TD}>
                <span className="font-medium text-foreground">{s.name}</span>
                <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                  {s.id}
                </div>
              </td>
              <td className={TD}>
                <Tag kind={s.enabled ? "armed" : "disarmed"}>
                  {s.enabled ? "armed" : "disarmed"}
                </Tag>{" "}
                ·{" "}
                <Tag kind={s.entry_mode === "auto" ? "auto" : "propose"}>
                  {s.entry_mode}
                </Tag>
              </td>
              <td className={TD}>
                <span className="text-[var(--fg-dim)]">
                  {s.account_label ?? "—"}
                </span>
              </td>
              <td className={TD_NUM}>{s.max_concurrent_positions ?? "—"}</td>
              <td className={TD_NUM}>
                {s.max_deployed_usd != null ? money(s.max_deployed_usd) : "—"}
              </td>
              <td className={TD}>
                <span className="text-[var(--fg-dim)]">
                  {s.universe || "—"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  );
}

type BookRow = { account: LabAccount; day_pnl: number; has_activity: boolean };

function BookRoster({
  family,
  onRoute,
}: {
  family: ActorFamily;
  onRoute: (r: LabRoute) => void;
}) {
  const [rows, setRows] = useState<BookRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchLabAccounts()
      .then(async (accounts) => {
        // Family membership from the v1 hardcoded map; fall back to the
        // canonical id list so a book still shows if labels drift.
        const known = new Set(
          (family === "llm" ? LLM_BOOK_ACCOUNTS : HUMAN_BOOK_ACCOUNTS).map(
            (a) => a.id,
          ),
        );
        const mine = accounts.filter(
          (a) => familyForAccount(a.id) === family || known.has(a.id),
        );
        const withPnl = await Promise.all(
          mine.map((account) =>
            fetchLabDayPnl(account.id)
              .then((r) => ({
                account,
                day_pnl: r.day_pnl,
                has_activity: r.has_activity,
              }))
              .catch(() => ({ account, day_pnl: 0, has_activity: false })),
          ),
        );
        if (!cancelled) setRows(withPnl);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "load failed"));
    return () => {
      cancelled = true;
    };
  }, [family]);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!rows) return <LoadingNote label="loading books…" />;
  if (rows.length === 0) return <EmptyNote>no {family} books mapped yet</EmptyNote>;

  return (
    <Frame>
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH}>book</th>
            <th className={TH}>family</th>
            <th className={TH}>account</th>
            <th className={TH_NUM}>day p&l</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ account, day_pnl, has_activity }) => (
            <tr
              key={account.id}
              className="cursor-pointer hover:bg-[var(--surface-hover)]"
              onClick={() =>
                onRoute({ tab: "live", family, actor: account.id })
              }
            >
              <td className={TD}>
                <span className="font-medium text-foreground">
                  {account.label}
                </span>
              </td>
              <td className={TD}>
                <Tag kind={family}>{family}</Tag>
              </td>
              <td className={TD}>
                <span className="font-mono text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                  {account.id}
                </span>
              </td>
              <td
                className={TD_NUM}
                style={{ color: tone(has_activity ? day_pnl : null) }}
              >
                {has_activity ? money(day_pnl, { sign: true }) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  );
}
