"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  Bot,
  FileText,
  FileUp,
  Search,
  Settings,
  Shield,
  Wallet,
} from "lucide-react";

import { useSnapshot } from "@/components/providers/snapshot-provider";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  SettingsSection,
  TerminalButton,
  TerminalField,
  TerminalInput,
  TerminalSelect,
  TerminalToggle,
} from "@/components/ui/terminal";
import {
  fetchSettings,
  saveSettings,
  SECRET_MASK,
  type AppSettings,
} from "@/lib/api";
import { DEFAULT_AI_SETTINGS, normalizeAiSettings } from "@/lib/ai-settings";
import { AiProviderSettings } from "@/components/settings/ai-provider-settings";
import { SettingsStrategiesPanel } from "@/components/settings/settings-strategies-panel";
import { SettingsTradesPanel } from "@/components/settings/settings-trades-panel";
import {
  DEFAULT_REPORTS,
  SettingsReportsPanel,
} from "@/components/settings/settings-reports-panel";
import {
  DEFAULT_RESEARCH,
  SettingsResearchPanel,
} from "@/components/settings/settings-research-panel";
import {
  DEFAULT_TRADING,
  TradingRiskSettings,
} from "@/components/settings/trading-risk-settings";
import { dispatchSettingsSaved } from "@/lib/settings-events";
import { cn } from "@/lib/utils";

type Props = {
  collapsed?: boolean;
};

type SettingsSection =
  | "brokers"
  | "execution"
  | "strategies"
  | "reports"
  | "ai"
  | "research"
  | "import";

const SECTIONS: {
  id: SettingsSection;
  label: string;
  icon: typeof Wallet;
}[] = [
  { id: "brokers", label: "Brokers", icon: Wallet },
  { id: "execution", label: "Execution / Risk", icon: Shield },
  { id: "strategies", label: "Strategies", icon: Activity },
  { id: "reports", label: "Reports", icon: FileText },
  { id: "ai", label: "AI", icon: Bot },
  { id: "research", label: "Research", icon: Search },
  { id: "import", label: "Import", icon: FileUp },
];

const EMPTY: AppSettings = {
  primary_broker: "alpaca",
  brokers: {
    alpaca: {
      enabled: true,
      api_key_id: "",
      api_secret_key: "",
      api_base_url: "https://paper-api.alpaca.markets",
      data_base_url: "https://data.alpaca.markets",
    },
    schwab: {
      enabled: false,
      api_key: "",
      app_secret: "",
      callback_url: "https://127.0.0.1:8182",
      account_hash: "",
    },
  },
  ai: DEFAULT_AI_SETTINGS,
  research: DEFAULT_RESEARCH,
  trading: DEFAULT_TRADING,
  reports: DEFAULT_REPORTS,
};

export function SettingsDialog({ collapsed = false }: Props) {
  const { refresh, brokerInfo } = useSnapshot();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<AppSettings>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [section, setSection] = useState<SettingsSection>("brokers");
  const [modelsRevision, setModelsRevision] = useState(0);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    fetchSettings()
      .then((data) =>
        setDraft({
          ...EMPTY,
          ...data,
          ai: normalizeAiSettings(data.ai),
          research: {
            ...DEFAULT_RESEARCH,
            ...data.research,
            model_roles: {
              ...DEFAULT_RESEARCH.model_roles,
              ...data.research?.model_roles,
            },
          },
          trading: { ...DEFAULT_TRADING, ...data.trading },
          reports: { ...DEFAULT_REPORTS, ...data.reports },
        }),
      )
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load settings"),
      )
      .finally(() => setLoading(false));
  }, [open]);

  useEffect(() => {
    if (open) setModelsRevision((n) => n + 1);
  }, [open]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await saveSettings(draft);
      setDraft({
        ...saved,
        ai: normalizeAiSettings(saved.ai),
        research: {
          ...DEFAULT_RESEARCH,
          ...saved.research,
          model_roles: {
            ...DEFAULT_RESEARCH.model_roles,
            ...saved.research?.model_roles,
          },
        },
        trading: { ...DEFAULT_TRADING, ...saved.trading },
        reports: { ...DEFAULT_REPORTS, ...saved.reports },
      });
      setMessage("Settings saved. Polling will use the updated brokers.");
      setModelsRevision((n) => n + 1);
      dispatchSettingsSaved();
      await refresh();
      setTimeout(() => setOpen(false), 600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  function secretInputValue(current: string) {
    return current === SECRET_MASK ? "" : current;
  }

  function secretInputPlaceholder(current: string, label: string) {
    return current === SECRET_MASK ? "Saved — type to replace" : label;
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        className={cn(
          "flex items-center gap-2 rounded-md text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground",
          collapsed ? "justify-center p-2" : "w-full px-2 py-2",
        )}
      >
        <Settings className="size-[18px]" strokeWidth={1.75} />
        {!collapsed && (
          <span className="text-[13px] font-medium">Settings</span>
        )}
      </DialogTrigger>
      <DialogContent className="flex h-[min(88vh,820px)] max-h-[88vh] w-[min(96vw,80rem)] flex-col gap-0 overflow-hidden border border-[var(--border)] bg-[var(--panel)] p-0 font-mono shadow-[var(--panel-shadow)] sm:!max-w-[min(96vw,80rem)]">
        <DialogHeader className="shrink-0 border-b border-border px-6 py-4">
          <DialogTitle className="font-mono text-[12px] tracking-[0.1em] uppercase text-foreground">
            Settings
          </DialogTitle>
          <DialogDescription className="font-mono text-[10px] text-[var(--fg-dim)]">
            Brokers, AI, research, and imported trade history. Stored locally in{" "}
            <code className="text-[10px]">data/</code>.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <nav className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-border bg-[var(--bg-inset)] p-2 sm:w-52 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r sm:p-4">
            {SECTIONS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => setSection(id)}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left font-mono text-[11px] tracking-[0.06em] uppercase transition-colors",
                  section === id
                    ? "border-l-2 border-[var(--c-accent)] bg-[var(--bg-hover)] text-foreground"
                    : "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
                )}
              >
                <Icon className="size-3.5 shrink-0 text-[var(--fg-faint)]" strokeWidth={1.75} />
                {label}
              </button>
            ))}
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto px-8 py-5">
        {loading ? (
          <p className="font-mono text-[10px] text-[var(--fg-dim)]">Loading…</p>
        ) : section === "brokers" ? (
          <div className="space-y-5">
              <TerminalField
                label="Primary broker"
                htmlFor="primary-broker"
                hint="Used for charts and market data (Alpaca recommended)."
              >
                <TerminalSelect
                  id="primary-broker"
                  value={draft.primary_broker}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      primary_broker: e.target.value as "alpaca" | "schwab",
                    }))
                  }
                >
                  <option value="alpaca">Alpaca</option>
                  <option value="schwab">Schwab</option>
                </TerminalSelect>
              </TerminalField>

              <SettingsSection variant="card">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-mono text-[11px] text-foreground">Alpaca</p>
                    <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                      Paper trading & market data
                    </p>
                  </div>
                  <TerminalToggle
                    checked={draft.brokers.alpaca.enabled}
                    onCheckedChange={(checked) =>
                      setDraft((d) => ({
                        ...d,
                        brokers: {
                          ...d.brokers,
                          alpaca: { ...d.brokers.alpaca, enabled: checked },
                        },
                      }))
                    }
                  />
                </div>
                <div className="grid gap-2">
                  <TerminalField label="API key ID" htmlFor="alpaca-key">
                    <TerminalInput
                      id="alpaca-key"
                      value={secretInputValue(draft.brokers.alpaca.api_key_id)}
                      placeholder={secretInputPlaceholder(
                        draft.brokers.alpaca.api_key_id,
                        "PK…",
                      )}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            alpaca: {
                              ...d.brokers.alpaca,
                              api_key_id: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                  <TerminalField label="API secret" htmlFor="alpaca-secret">
                    <TerminalInput
                      id="alpaca-secret"
                      type="password"
                      value={secretInputValue(draft.brokers.alpaca.api_secret_key)}
                      placeholder={secretInputPlaceholder(
                        draft.brokers.alpaca.api_secret_key,
                        "Secret key",
                      )}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            alpaca: {
                              ...d.brokers.alpaca,
                              api_secret_key: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                  <TerminalField label="API base URL" htmlFor="alpaca-base">
                    <TerminalInput
                      id="alpaca-base"
                      value={draft.brokers.alpaca.api_base_url}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            alpaca: {
                              ...d.brokers.alpaca,
                              api_base_url: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                </div>
              </SettingsSection>

              <SettingsSection variant="card">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-mono text-[11px] text-foreground">Schwab</p>
                    <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                      OAuth — run login script after saving credentials
                    </p>
                  </div>
                  <TerminalToggle
                    checked={draft.brokers.schwab.enabled}
                    onCheckedChange={(checked) =>
                      setDraft((d) => ({
                        ...d,
                        brokers: {
                          ...d.brokers,
                          schwab: { ...d.brokers.schwab, enabled: checked },
                        },
                      }))
                    }
                  />
                </div>
                {brokerInfo?.brokers?.schwab?.auth && (
                  <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                    {brokerInfo.brokers.schwab.auth.message}
                  </p>
                )}
                <div className="grid gap-2">
                  <TerminalField label="App key" htmlFor="schwab-key">
                    <TerminalInput
                      id="schwab-key"
                      value={secretInputValue(draft.brokers.schwab.api_key)}
                      placeholder={secretInputPlaceholder(
                        draft.brokers.schwab.api_key,
                        "App key",
                      )}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            schwab: {
                              ...d.brokers.schwab,
                              api_key: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                  <TerminalField label="App secret" htmlFor="schwab-secret">
                    <TerminalInput
                      id="schwab-secret"
                      type="password"
                      value={secretInputValue(draft.brokers.schwab.app_secret)}
                      placeholder={secretInputPlaceholder(
                        draft.brokers.schwab.app_secret,
                        "App secret",
                      )}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            schwab: {
                              ...d.brokers.schwab,
                              app_secret: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                  <TerminalField label="Callback URL" htmlFor="schwab-callback">
                    <TerminalInput
                      id="schwab-callback"
                      value={draft.brokers.schwab.callback_url}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            schwab: {
                              ...d.brokers.schwab,
                              callback_url: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                  <TerminalField label="Account hash (optional)" htmlFor="schwab-hash">
                    <TerminalInput
                      id="schwab-hash"
                      value={draft.brokers.schwab.account_hash}
                      onChange={(e) =>
                        setDraft((d) => ({
                          ...d,
                          brokers: {
                            ...d.brokers,
                            schwab: {
                              ...d.brokers.schwab,
                              account_hash: e.target.value,
                            },
                          },
                        }))
                      }
                    />
                  </TerminalField>
                </div>
                <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                  After saving, authenticate:{" "}
                  <code className="text-[10px]">
                    python -m scripts.schwab_login
                  </code>
                </p>
              </SettingsSection>

              <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                Enable both brokers to see a combined overview with separate
                account sections.
              </p>
          </div>
        ) : section === "execution" ? (
          <TradingRiskSettings
            value={draft.trading}
            onChange={(trading) => setDraft((d) => ({ ...d, trading }))}
          />
        ) : section === "strategies" ? (
          <SettingsStrategiesPanel />
        ) : section === "reports" ? (
          <SettingsReportsPanel
            value={draft.reports}
            onChange={(reports) => setDraft((d) => ({ ...d, reports }))}
          />
        ) : section === "ai" ? (
          <div className="space-y-4">
              <AiProviderSettings
                value={draft.ai}
                onChange={(ai) => setDraft((d) => ({ ...d, ai }))}
                modelsRevision={modelsRevision}
              />
              <p className="font-mono text-[10px] text-[var(--fg-dim)]">
                Keys are stored locally in{" "}
                <code className="text-[10px]">data/settings.json</code>. Custom
                base URLs support proxies, Azure OpenAI, and compatible gateways.
              </p>
          </div>
        ) : section === "research" ? (
          <SettingsResearchPanel
            value={draft.research}
            ai={draft.ai}
            onChange={(research) => setDraft((d) => ({ ...d, research }))}
          />
        ) : (
          <SettingsTradesPanel />
        )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-6 py-4">
        {error && (
          <p className="mb-2 font-mono text-[11px] text-[var(--c-red)]">{error}</p>
        )}
        {message && (
          <p className="mb-2 font-mono text-[11px] text-[var(--fg-dim)]">{message}</p>
        )}

        <DialogFooter className="sm:justify-end">
          {section === "import" || section === "strategies" ? (
            // These sections save their own changes inline (per-card / per-action),
            // so the global Save doesn't apply — offer a plain Done to close instead
            // of a disabled Save + a misleading "Cancel".
            <TerminalButton type="button" onClick={() => setOpen(false)} disabled={saving}>
              Done
            </TerminalButton>
          ) : (
            <>
              <TerminalButton
                type="button"
                onClick={() => setOpen(false)}
                disabled={saving}
              >
                Cancel
              </TerminalButton>
              <TerminalButton
                type="button"
                variant="primary"
                onClick={handleSave}
                disabled={saving || loading}
              >
                {saving ? "Saving…" : "Save"}
              </TerminalButton>
            </>
          )}
        </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
