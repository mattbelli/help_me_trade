"use client";

import { useEffect, useState } from "react";
import { Settings } from "lucide-react";

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  fetchSettings,
  saveSettings,
  SECRET_MASK,
  type AppSettings,
} from "@/lib/api";
import { DEFAULT_AI_SETTINGS, normalizeAiSettings } from "@/lib/ai-settings";
import { AiProviderSettings } from "@/components/settings/ai-provider-settings";
import { cn } from "@/lib/utils";

type Props = {
  collapsed?: boolean;
};

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
};

export function SettingsDialog({ collapsed = false }: Props) {
  const { refresh, brokerInfo } = useSnapshot();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<AppSettings>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

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
        }),
      )
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load settings"),
      )
      .finally(() => setLoading(false));
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
      });
      setMessage("Settings saved. Polling will use the updated brokers.");
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
      <DialogContent className="max-h-[85vh] overflow-y-auto bg-popover/95 backdrop-blur-md sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Brokers, API keys, and AI providers. Stored locally in{" "}
            <code className="text-xs">data/settings.json</code>.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <Tabs defaultValue="brokers">
            <TabsList className="w-full">
              <TabsTrigger value="brokers" className="flex-1">
                Brokers
              </TabsTrigger>
              <TabsTrigger value="ai" className="flex-1">
                AI
              </TabsTrigger>
              <TabsTrigger value="research" className="flex-1">
                Research
              </TabsTrigger>
            </TabsList>

            <TabsContent value="brokers" className="space-y-5 pt-2">
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
            </TabsContent>

            <TabsContent value="ai" className="space-y-4 pt-2">
              <AiProviderSettings
                value={draft.ai}
                onChange={(ai) => setDraft((d) => ({ ...d, ai }))}
              />
              <p className="text-xs text-muted-foreground">
                Keys are stored locally in{" "}
                <code className="text-[11px]">data/settings.json</code>. Custom
                base URLs support proxies, Azure OpenAI, and compatible gateways.
              </p>
            </TabsContent>

            <TabsContent value="research" className="space-y-4 pt-2">
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
            </TabsContent>
          </Tabs>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}
        {message && (
          <p className="text-sm text-muted-foreground">{message}</p>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} disabled={saving || loading}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
