"use client";

// OVERVIEW — the 10-second read. Tile strip (system posture) → live modules
// (what's waiting on me / what happened today) → nothing here is fabricated:
// every number is a live API response or an explicit honest empty state.

import { useEffect, useState } from "react";

import {
  fetchLabAccounts,
  fetchLabDayPnl,
  fetchLabExecutions,
  fetchLabJobs,
  fetchLabPendingProposals,
  fetchLabStrategies,
  type LabAccount,
  type LabExecution,
  type LabJobHealth,
  type LabPendingProposal,
  type LabStrategy,
} from "@/lib/api";

import {
  etClock,
  familyForAccount,
  isTodayET,
  type LabRoute,
} from "./lab-nav";
import {
  EmptyNote,
  ErrorNote,
  Frame,
  LoadingNote,
  ModuleFoot,
  ModuleHead,
  money,
  StatBox,
  StatStrip,
  Ticker,
  tone,
} from "./lab-ui";

type DayPnl = { id: string; label: string; day_pnl: number; has_activity: boolean };

type Data = {
  strategies: LabStrategy[];
  accounts: LabAccount[];
  pending: LabPendingProposal[];
  execs: LabExecution[];
  jobs: LabJobHealth[];
  dayPnls: DayPnl[];
};

const SIDE_COLOR = (side: string) =>
  side.toLowerCase() === "sell" ? "var(--neg)" : "var(--pos)";

export function OverviewTab({ onRoute }: { onRoute: (r: LabRoute) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [strategies, accounts, pending, execs, jobs] = await Promise.all([
          fetchLabStrategies(),
          fetchLabAccounts(),
          fetchLabPendingProposals(),
          fetchLabExecutions(100),
          fetchLabJobs(),
        ]);
        const dayPnls = await Promise.all(
          accounts.map((a) =>
            fetchLabDayPnl(a.id)
              .then((r) => ({
                id: a.id,
                label: a.label,
                day_pnl: r.day_pnl,
                has_activity: r.has_activity,
              }))
              .catch(() => ({
                id: a.id,
                label: a.label,
                day_pnl: 0,
                has_activity: false,
              })),
          ),
        );
        if (!cancelled)
          setData({ strategies, accounts, pending, execs, jobs, dayPnls });
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "failed to load");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!data) return <LoadingNote label="loading cockpit…" />;

  const { strategies, pending, execs, jobs, dayPnls } = data;
  const armedAuto = strategies.filter(
    (s) => s.enabled && s.entry_mode === "auto",
  );
  const armedPropose = strategies.filter(
    (s) => s.enabled && s.entry_mode === "propose",
  );
  const disarmed = strategies.filter((s) => !s.enabled);
  const netDay = dayPnls.reduce((sum, d) => sum + d.day_pnl, 0);

  const todayFills = execs
    .filter((e) => isTodayET(e.created_at) || isTodayET(e.submitted_at))
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  const todayJobs = jobs
    .filter((j) => isTodayET(j.last_success_at))
    .sort(
      (a, b) =>
        new Date(b.last_success_at ?? 0).getTime() -
        new Date(a.last_success_at ?? 0).getTime(),
    );

  return (
    <div className="space-y-6">
      {/* COCKPIT TILE STRIP — every number cross-foots with the roster below. */}
      <StatStrip>
        <StatBox
          k="armed · auto"
          v={armedAuto.length}
          s="acts within caps"
          color="var(--warn)"
        />
        <StatBox
          k="armed · propose"
          v={armedPropose.length}
          s="asks the phone first"
        />
        <StatBox
          k="disarmed"
          v={disarmed.length}
          s="not trading"
          color="var(--fg-dim)"
        />
        <StatBox
          k="on probation"
          v={<span className="text-[var(--fg-faint)]">—</span>}
          s="not tracked yet"
        />
        <StatBox
          k="waiting on you"
          v={pending.length}
          s="phone approvals"
          color="var(--c-accent)"
        />
        <StatBox
          k="net day p&l · books"
          v={money(netDay, { sign: true })}
          s="net-cash · source of truth"
          color={tone(netDay)}
        />
      </StatStrip>

      {/* LIVE MODULES */}
      <div className="grid grid-cols-1 gap-3.5 lg:grid-cols-2 xl:grid-cols-4">
        {/* waiting on you · phone */}
        <Frame attn>
          <ModuleHead
            label="waiting on you · phone"
            count={pending.length}
            countColor="var(--c-accent)"
            meta="approve on phone"
          />
          {pending.length === 0 ? (
            <EmptyNote>nothing waiting — you&apos;re clear</EmptyNote>
          ) : (
            <table className="w-full">
              <tbody>
                {pending.slice(0, 6).map((p) => (
                  <tr
                    key={p.id}
                    className="border-b border-[var(--line-divider)] last:border-b-0"
                  >
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5 text-[length:var(--fs-body)]">
                        <Ticker symbol={p.symbol} />
                        <span style={{ color: SIDE_COLOR(p.side) }}>
                          {p.side.toLowerCase()}
                        </span>
                        <span className="tabular-nums text-foreground">
                          {p.qty}
                        </span>
                      </span>
                      <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                        {p.summary}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ModuleFoot>
            approvals happen on the phone — this is the read-back.
          </ModuleFoot>
        </Frame>

        {/* today's fills */}
        <Frame>
          <ModuleHead
            label="today's fills"
            count={todayFills.length}
            meta="via the chokepoint"
          />
          {todayFills.length === 0 ? (
            <EmptyNote>no fills yet today</EmptyNote>
          ) : (
            <table className="w-full">
              <tbody>
                {todayFills.slice(0, 4).map((e) => (
                  <tr
                    key={e.id}
                    className="border-b border-[var(--line-divider)] last:border-b-0"
                  >
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-1.5 text-[length:var(--fs-body)]">
                        <Ticker symbol={e.symbol} />
                        <span style={{ color: SIDE_COLOR(e.side) }}>
                          {e.side.toLowerCase()}
                        </span>
                        <span className="tabular-nums text-foreground">
                          {(e.filled_qty ?? e.qty).toLocaleString(undefined, {
                            maximumFractionDigits: 2,
                          })}
                        </span>
                      </span>
                      <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                        {e.status} · {e.execution_mode}
                      </div>
                    </td>
                    <td className="px-3 py-1.5 text-right align-top tabular-nums text-[length:var(--fs-body)]">
                      {e.avg_fill_price != null
                        ? money(e.avg_fill_price, { cents: true })
                        : "—"}
                      <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                        {etClock(e.submitted_at ?? e.created_at)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ModuleFoot>latest fills · net-cash realized is truth</ModuleFoot>
        </Frame>

        {/* today's p&l by book */}
        <Frame>
          <ModuleHead
            label="today's p&l · by book"
            meta={
              <span
                className="tabular-nums"
                style={{ color: tone(netDay) }}
              >
                Σ {money(netDay, { sign: true })}
              </span>
            }
          />
          <table className="w-full">
            <tbody>
              {dayPnls.map((d) => (
                <tr
                  key={d.id}
                  className="cursor-pointer border-b border-[var(--line-divider)] last:border-b-0 hover:bg-[var(--surface-hover)]"
                  onClick={() =>
                    onRoute({
                      tab: "live",
                      family: familyForAccount(d.id) ?? "llm",
                      actor: d.id,
                    })
                  }
                >
                  <td className="px-3 py-1.5 text-[length:var(--fs-body)] text-foreground">
                    {d.label}
                  </td>
                  <td
                    className="px-3 py-1.5 text-right tabular-nums text-[length:var(--fs-body)]"
                    style={{ color: tone(d.has_activity ? d.day_pnl : null) }}
                  >
                    {d.has_activity ? money(d.day_pnl, { sign: true }) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Frame>

        {/* agents ran today (system jobs — honest subset) */}
        <Frame>
          <ModuleHead
            label="agents ran today"
            count={todayJobs.length}
            meta="system jobs"
          />
          {todayJobs.length === 0 ? (
            <EmptyNote>no job runs recorded today</EmptyNote>
          ) : (
            <table className="w-full">
              <tbody>
                {todayJobs.slice(0, 6).map((j) => (
                  <tr
                    key={j.name}
                    className="border-b border-[var(--line-divider)] last:border-b-0"
                  >
                    <td className="px-3 py-1.5 text-[length:var(--fs-body)] text-foreground">
                      {j.name}
                      {j.consecutive_failures > 0 ? (
                        <span className="ml-1.5 text-[length:var(--fs-table)] text-[var(--neg)]">
                          {j.consecutive_failures} fail
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-[length:var(--fs-table)] text-[var(--fg-faint)]">
                      {etClock(j.last_success_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <ModuleFoot>
            system scheduled jobs — no per-agent run registry yet
          </ModuleFoot>
        </Frame>
      </div>

      <div className="text-[length:var(--fs-table)] text-[var(--fg-faint)]">
        cockpit reads live API state · &ldquo;on probation&rdquo; has no backend
        concept yet (shown honest-empty) · roster + book detail live under{" "}
        <button
          type="button"
          className="text-[var(--c-accent)] hover:underline"
          onClick={() =>
            onRoute({ tab: "live", family: "mechanical", actor: null })
          }
        >
          live
        </button>
        .
      </div>
    </div>
  );
}
