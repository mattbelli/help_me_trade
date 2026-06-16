import { API_BASE, apiFetch } from "./api";

export type RuleConditions = Record<string, unknown>;
export type RuleAction = {
  type: string;
  message?: string;
  [key: string]: unknown;
};

export type Rule = {
  id: string;
  name: string;
  enabled: boolean;
  conditions: RuleConditions;
  action: RuleAction;
  created_at: string;
  updated_at: string;
};

export type RuleEvent = {
  id: number;
  rule_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type RulePreset = {
  id: string;
  label: string;
  description: string;
  build: (params: { threshold?: number }) => {
    name: string;
    conditions: RuleConditions;
    action: RuleAction;
  };
};

export const RULE_PRESETS: RulePreset[] = [
  {
    id: "has-positions",
    label: "Has open positions",
    description: "Triggers when at least one position is open.",
    build: () => ({
      name: "Has open positions",
      conditions: {
        all: [
          {
            fact: "position_count",
            operator: "greaterThan",
            value: 0,
          },
        ],
      },
      action: {
        type: "notify",
        message: "You have open positions in the portfolio.",
      },
    }),
  },
  {
    id: "position-count",
    label: "Position count above threshold",
    description: "Alert when holding more than N symbols.",
    build: ({ threshold = 3 }) => ({
      name: `More than ${threshold} positions`,
      conditions: {
        all: [
          {
            fact: "position_count",
            operator: "greaterThan",
            value: threshold,
          },
        ],
      },
      action: {
        type: "notify",
        message: `Portfolio has more than ${threshold} open positions.`,
      },
    }),
  },
  {
    id: "portfolio-value",
    label: "Portfolio value above threshold",
    description: "Alert when account equity exceeds a dollar amount.",
    build: ({ threshold = 100000 }) => ({
      name: `Portfolio above $${threshold.toLocaleString()}`,
      conditions: {
        all: [
          {
            fact: "portfolio_value",
            operator: "greaterThan",
            value: threshold,
          },
        ],
      },
      action: {
        type: "notify",
        message: `Portfolio value is above $${threshold.toLocaleString()}.`,
      },
    }),
  },
  {
    id: "unrealized-loss",
    label: "Unrealized loss beyond threshold",
    description: "Alert when total open P/L drops below a negative dollar amount.",
    build: ({ threshold = 500 }) => ({
      name: `Unrealized loss beyond $${threshold}`,
      conditions: {
        all: [
          {
            fact: "total_unrealized_pl",
            operator: "lessThan",
            value: -Math.abs(threshold),
          },
        ],
      },
      action: {
        type: "notify",
        message: `Total unrealized P/L is below -$${threshold}.`,
      },
    }),
  },
];

export async function listRules(): Promise<Rule[]> {
  const res = await apiFetch(`${API_BASE}/api/rules`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load rules (${res.status})`);
  const data = (await res.json()) as { rules: Rule[] };
  return data.rules;
}

export async function listRuleEvents(limit = 50): Promise<RuleEvent[]> {
  const res = await apiFetch(`${API_BASE}/api/rules/events?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load rule events (${res.status})`);
  const data = (await res.json()) as { events: RuleEvent[] };
  return data.events;
}

export async function createRule(body: {
  id?: string;
  name: string;
  enabled?: boolean;
  conditions: RuleConditions;
  action: RuleAction;
}): Promise<Rule> {
  const res = await apiFetch(`${API_BASE}/api/rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to create rule (${res.status})`);
  }
  return res.json() as Promise<Rule>;
}

export async function updateRule(
  id: string,
  body: Partial<Pick<Rule, "name" | "enabled" | "conditions" | "action">>,
): Promise<Rule> {
  const res = await apiFetch(`${API_BASE}/api/rules/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to update rule (${res.status})`);
  }
  return res.json() as Promise<Rule>;
}

export async function deleteRule(id: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/api/rules/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Failed to delete rule (${res.status})`);
  }
}
