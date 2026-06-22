"use client";

import { create } from "zustand";

import type { Snapshot } from "@/lib/api";

const STORAGE_KEY = "hmt:dashboard-prefs:v1";

type PersistedPrefs = {
  selectedAccountId: string;
  privacyMode: boolean;
};

const DEFAULTS: PersistedPrefs = {
  selectedAccountId: "all",
  privacyMode: false,
};

export type AccountView = {
  label: string;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  unrealized_pl: number;
};

function loadPrefs(): PersistedPrefs {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<PersistedPrefs>;
    return {
      selectedAccountId:
        typeof parsed.selectedAccountId === "string"
          ? parsed.selectedAccountId
          : DEFAULTS.selectedAccountId,
      privacyMode:
        typeof parsed.privacyMode === "boolean"
          ? parsed.privacyMode
          : DEFAULTS.privacyMode,
    };
  } catch {
    return DEFAULTS;
  }
}

function persistPrefs(prefs: PersistedPrefs): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // ignore quota / private mode
  }
}

export function resolveAccountView(
  snapshot: Snapshot | null,
  selectedAccountId: string,
): AccountView | null {
  if (!snapshot) return null;

  if (selectedAccountId === "all") {
    const summary = snapshot.all_accounts_summary;
    if (summary) {
      return {
        label: "All accounts",
        cash: summary.cash,
        buying_power: summary.buying_power,
        portfolio_value: summary.portfolio_value,
        unrealized_pl: summary.unrealized_pl,
      };
    }
  } else {
    const entry = snapshot.snapshots?.find(
      (s) => s.account_id === selectedAccountId,
    );
    if (entry) {
      return {
        label: entry.account_label ?? entry.broker,
        cash: entry.account.cash,
        buying_power: entry.account.buying_power,
        portfolio_value: entry.account.portfolio_value,
        unrealized_pl: entry.positions.reduce(
          (sum, p) => sum + p.unrealized_pl,
          0,
        ),
      };
    }
  }

  const unrealized_pl = snapshot.positions.reduce(
    (sum, p) => sum + p.unrealized_pl,
    0,
  );
  const defaultEntry = snapshot.snapshots?.find((s) => s.is_default);
  return {
    label: defaultEntry?.account_label ?? defaultEntry?.broker ?? "Account",
    cash: snapshot.account.cash,
    buying_power: snapshot.account.buying_power,
    portfolio_value: snapshot.account.portfolio_value,
    unrealized_pl,
  };
}

export function maskMoney(
  value: number,
  privacy: boolean,
  fmt: (n: number) => string,
): string {
  return privacy ? "••••••" : fmt(value);
}

type DashboardPrefs = {
  selectedAccountId: string;
  privacyMode: boolean;
  setSelectedAccountId: (id: string) => void;
  togglePrivacy: () => void;
};

const initialPrefs =
  typeof window !== "undefined" ? loadPrefs() : DEFAULTS;

export const useDashboardPrefs = create<DashboardPrefs>((set, get) => ({
  selectedAccountId: initialPrefs.selectedAccountId,
  privacyMode: initialPrefs.privacyMode,

  setSelectedAccountId: (id) => {
    set({ selectedAccountId: id });
    persistPrefs({ selectedAccountId: id, privacyMode: get().privacyMode });
  },

  togglePrivacy: () => {
    const privacyMode = !get().privacyMode;
    set({ privacyMode });
    persistPrefs({ selectedAccountId: get().selectedAccountId, privacyMode });
  },
}));
