"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { relativeTime } from "@/lib/relative-time";
import {
  deleteRule,
  listRuleEvents,
  listRules,
  updateRule,
  type Rule,
  type RuleConditions,
  type RuleEvent,
} from "@/lib/rules";
import type { WidgetProps } from "@/lib/workspace/widget-registry";
import { cn } from "@/lib/utils";

const POLL_MS = 15_000;

const OPERATOR_SYMBOLS: Record<string, string> = {
  equal: "=",
  notEqual: "!=",
  lessThan: "<",
  lessThanInclusive: "≤",
  greaterThan: ">",
  greaterThanInclusive: "≥",
  in: "in",
  contains: "contains",
};

type LeafCondition = {
  fact?: string;
  operator?: string;
  value?: unknown;
};

type GroupCondition = {
  all?: unknown[];
  any?: unknown[];
  not?: unknown;
};

function isLeafCondition(value: unknown): value is LeafCondition {
  return typeof value === "object" && value !== null && "fact" in value;
}

function isGroupCondition(value: unknown): value is GroupCondition {
  return (
    typeof value === "object" &&
    value !== null &&
    ("all" in value || "any" in value || "not" in value)
  );
}

function formatLeafCondition(leaf: LeafCondition): string {
  const fact = leaf.fact ?? "fact";
  const op = leaf.operator ?? "equal";
  const value = leaf.value;

  if (op === "equal" && value === true) return `${fact} = true`;
  if (op === "equal" && value === false) return `${fact} = false`;

  const symbol = OPERATOR_SYMBOLS[op] ?? op;
  const valueStr =
    value === undefined || value === null ? "—" : String(value);
  return `${fact} ${symbol} ${valueStr}`;
}

export function summarizeConditions(conditions: RuleConditions): string {
  if (!conditions || typeof conditions !== "object") {
    return conditions == null ? "—" : String(conditions);
  }

  if (isLeafCondition(conditions)) {
    return formatLeafCondition(conditions);
  }

  if (isGroupCondition(conditions)) {
    if (conditions.all?.length) {
      return conditions.all
        .map((child) => summarizeConditions(child as RuleConditions))
        .join(" AND ");
    }
    if (conditions.any?.length) {
      return conditions.any
        .map((child) => summarizeConditions(child as RuleConditions))
        .join(" OR ");
    }
    if (conditions.not) {
      return `NOT (${summarizeConditions(conditions.not as RuleConditions)})`;
    }
  }

  try {
    return JSON.stringify(conditions);
  } catch {
    return "—";
  }
}

function buildLastFiredMap(events: RuleEvent[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const event of events) {
    const prev = map.get(event.rule_id);
    if (!prev || Date.parse(event.created_at) > Date.parse(prev)) {
      map.set(event.rule_id, event.created_at);
    }
  }
  return map;
}

export function ScansWidget({}: WidgetProps) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [events, setEvents] = useState<RuleEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const lastFired = useMemo(() => buildLastFiredMap(events), [events]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [ruleItems, eventItems] = await Promise.all([
          listRules(),
          listRuleEvents(50),
        ]);
        if (cancelled) return;
        setRules(ruleItems);
        setEvents(eventItems);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load rules");
        setRules([]);
        setEvents([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    const timer = setInterval(load, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const handleToggle = useCallback(async (rule: Rule, enabled: boolean) => {
    setRules((prev) =>
      prev.map((item) =>
        item.id === rule.id ? { ...item, enabled } : item,
      ),
    );
    try {
      await updateRule(rule.id, { enabled });
    } catch (err) {
      setRules((prev) =>
        prev.map((item) =>
          item.id === rule.id ? { ...item, enabled: rule.enabled } : item,
        ),
      );
      setError(err instanceof Error ? err.message : "Failed to update rule");
    }
  }, []);

  const handleDelete = useCallback(async (rule: Rule) => {
    setRules((prev) => prev.filter((item) => item.id !== rule.id));
    try {
      await deleteRule(rule.id);
    } catch (err) {
      setRules((prev) => {
        if (prev.some((item) => item.id === rule.id)) return prev;
        return [...prev, rule].sort((a, b) =>
          a.name.localeCompare(b.name),
        );
      });
      setError(err instanceof Error ? err.message : "Failed to delete rule");
    }
  }, []);

  if (loading && rules.length === 0) {
    return <Skeleton className="m-3 h-full min-h-24 rounded-md" />;
  }

  if (error && rules.length === 0) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--c-red)]">{error}</p>
    );
  }

  if (rules.length === 0) {
    return (
      <p className="p-3 font-mono text-xs text-[var(--fg-dim)]">
        No rules yet — ask the copilot to &apos;alert me when…&apos; to arm
        one.
      </p>
    );
  }

  return (
    <div className="h-full overflow-auto px-1.5 py-1">
      {error ? (
        <p className="mb-1 px-2 font-mono text-[10px] text-[var(--c-red)]">
          {error}
        </p>
      ) : null}
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead>
          <tr>
            {(["Name", "Condition", "Armed", "Last fired", ""] as const).map(
              (h, idx) => (
                <th
                  key={h || "actions"}
                  className={cn(
                    "border-b border-[var(--border)] px-2 py-1.5 text-[9.5px] font-medium tracking-[0.05em] text-[var(--fg-faint)] uppercase",
                    idx === 0 || idx === 1 ? "text-left" : "text-center",
                    h === "" && "w-8",
                  )}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => {
            const firedAt = lastFired.get(rule.id);
            return (
              <tr
                key={rule.id}
                className="transition-colors hover:bg-[var(--bg-hover)]"
              >
                <td className="max-w-[8rem] truncate border-b border-[var(--border)] px-2 py-1.5 font-medium text-[var(--c-accent)]">
                  {rule.name}
                </td>
                <td
                  className="max-w-[14rem] truncate border-b border-[var(--border)] px-2 py-1.5 text-[11px] text-[var(--fg-dim)]"
                  title={summarizeConditions(rule.conditions)}
                >
                  {summarizeConditions(rule.conditions)}
                </td>
                <td className="border-b border-[var(--border)] px-2 py-1.5 text-center">
                  <Switch
                    size="sm"
                    checked={rule.enabled}
                    onCheckedChange={(checked) =>
                      void handleToggle(rule, checked)
                    }
                    aria-label={`${rule.enabled ? "Disarm" : "Arm"} ${rule.name}`}
                  />
                </td>
                <td className="border-b border-[var(--border)] px-2 py-1.5 text-center text-[11px] tabular-nums text-[var(--fg-faint)]">
                  {firedAt ? relativeTime(firedAt) : "—"}
                </td>
                <td className="border-b border-[var(--border)] px-1 py-1.5 text-center">
                  <button
                    type="button"
                    className="inline-flex size-5 items-center justify-center rounded text-[11px] text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--c-red)]"
                    aria-label={`Delete ${rule.name}`}
                    onClick={() => void handleDelete(rule)}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
