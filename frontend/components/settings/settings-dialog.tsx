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
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
  research: { searxng_url: "" },
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
            searxng_url: data.research?.searxng_url ?? "",
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
          searxng_url: saved.research?.searxng_url ?? "",
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
      <DialogContent className="flex h-[min(88vh,820px)] max-h-[88vh] w-[min(96vw,80rem)] flex-col gap-0 overflow-hidden bg-popover/95 p-0 backdrop-blur-md sm:!max-w-[min(96vw,80rem)]">
        <DialogHeader className="shrink-0 border-b border-border px-6 py-4">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Brokers, AI, research, and imported trade history. Stored locally in{" "}
            <code className="text-xs">data/</code>.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          <nav className="flex shrink-0 gap-0.5 overflow-x-auto border-b border-border bg-muted/20 p-2 sm:w-52 sm:flex-col sm:overflow-visible sm:border-b-0 sm:border-r sm:p-4">
            {SECTIONS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => setSection(id)}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-[13px] font-medium transition-colors",
                  section === id
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0 opacity-80" strokeWidth={1.75} />
                {label}
              </button>
            ))}
          </nav>

          <div className="min-h-0 flex-1 overflow-y-auto px-8 py-5">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : section === "brokers" ? (
          <div className="space-y-5">
              <div className="space-y-2">
                <Label htmlFor="primary-broker">Primary broker</Label>
                <p className="text-xs text-muted-foreground">
                  Used for charts and market data (Alpaca recommended).
                </p>
                <select
                  id="primary-broker"
                  className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
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
                </select>
              </div>

              <div className="space-y-3 rounded-lg border border-border p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">Alpaca</p>
                    <p className="text-xs text-muted-foreground">
                      Paper trading & market data
                    </p>
                  </div>
                  <Switch
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
                  <Label htmlFor="alpaca-key">API key ID</Label>
                  <Input
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
                  <Label htmlFor="alpaca-secret">API secret</Label>
                  <Input
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
                  <Label htmlFor="alpaca-base">API base URL</Label>
                  <Input
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
                </div>
              </div>

              <div className="space-y-3 rounded-lg border border-border p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">Schwab</p>
                    <p className="text-xs text-muted-foreground">
                      OAuth — run login script after saving credentials
                    </p>
                  </div>
                  <Switch
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
                  <p className="text-xs text-muted-foreground">
                    {brokerInfo.brokers.schwab.auth.message}
                  </p>
                )}
                <div className="grid gap-2">
                  <Label htmlFor="schwab-key">App key</Label>
                  <Input
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
                  <Label htmlFor="schwab-secret">App secret</Label>
                  <Input
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
                  <Label htmlFor="schwab-callback">Callback URL</Label>
                  <Input
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
                  <Label htmlFor="schwab-hash">Account hash (optional)</Label>
                  <Input
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
                </div>
                <p className="text-xs text-muted-foreground">
                  After saving, authenticate:{" "}
                  <code className="text-[11px]">
                    python -m scripts.schwab_login
                  </code>
                </p>
              </div>

              <p className="text-xs text-muted-foreground">
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
              <p className="text-xs text-muted-foreground">
                Keys are stored locally in{" "}
                <code className="text-[11px]">data/settings.json</code>. Custom
                base URLs support proxies, Azure OpenAI, and compatible gateways.
              </p>
          </div>
        ) : section === "research" ? (
          <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="searxng-url">SearXNG URL (optional)</Label>
                <Input
                  id="searxng-url"
                  value={draft.research?.searxng_url ?? ""}
                  placeholder="http://localhost:8080"
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      research: { searxng_url: e.target.value.trim() },
                    }))
                  }
                />
                <p className="text-xs text-muted-foreground">
                  When set, deep research uses live web search instead of mock
                  snippets. You can also set{" "}
                  <code className="text-[11px]">SEARXNG_URL</code> in{" "}
                  <code className="text-[11px]">.env</code>. Env wins if both are
                  set.
                </p>
              </div>
          </div>
        ) : (
          <SettingsTradesPanel />
        )}
          </div>
        </div>

        <div className="shrink-0 border-t border-border px-6 py-4">
        {error && <p className="mb-2 text-sm text-destructive">{error}</p>}
        {message && (
          <p className="mb-2 text-sm text-muted-foreground">{message}</p>
        )}

        <DialogFooter className="sm:justify-end">
          {section === "import" || section === "strategies" ? (
            // These sections save their own changes inline (per-card / per-action),
            // so the global Save doesn't apply — offer a plain Done to close instead
            // of a disabled Save + a misleading "Cancel".
            <Button type="button" onClick={() => setOpen(false)} disabled={saving}>
              Done
            </Button>
          ) : (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
                disabled={saving}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleSave}
                disabled={saving || loading}
              >
                {saving ? "Saving…" : "Save"}
              </Button>
            </>
          )}
        </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
