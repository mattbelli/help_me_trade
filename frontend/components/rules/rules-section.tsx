"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  createRule,
  deleteRule,
  listRuleEvents,
  listRules,
  RULE_PRESETS,
  updateRule,
  type Rule,
  type RuleEvent,
} from "@/lib/rules";

type Props = {
  /** When false, omit outer Card wrappers (e.g. embedded in Automations page). */
  showCards?: boolean;
  onCountsChange?: (counts: { enabled: number; total: number }) => void;
};

export function RulesSection({ showCards = true, onCountsChange }: Props) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [events, setEvents] = useState<RuleEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [presetId, setPresetId] = useState(RULE_PRESETS[0]?.id ?? "");
  const [threshold, setThreshold] = useState("3");
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ruleItems, eventItems] = await Promise.all([
        listRules(),
        listRuleEvents(40),
      ]);
      setRules(ruleItems);
      setEvents(eventItems);
      onCountsChange?.({
        enabled: ruleItems.filter((r) => r.enabled).length,
        total: ruleItems.length,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load rules");
    } finally {
      setLoading(false);
    }
  }, [onCountsChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleToggle(rule: Rule, enabled: boolean) {
    setError(null);
    try {
      await updateRule(rule.id, { enabled });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update rule");
    }
  }

  async function handleDelete(rule: Rule) {
    if (!window.confirm(`Delete rule "${rule.name}"?`)) return;
    setError(null);
    try {
      await deleteRule(rule.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete rule");
    }
  }

  async function handleCreate() {
    const preset = RULE_PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    const numeric = Number(threshold);
    const built = preset.build({
      threshold: Number.isFinite(numeric) ? numeric : undefined,
    });
    setSaving(true);
    setError(null);
    try {
      await createRule({
        name: built.name,
        conditions: built.conditions,
        action: built.action,
        enabled: true,
      });
      setDialogOpen(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create rule");
    } finally {
      setSaving(false);
    }
  }

  const selectedPreset = RULE_PRESETS.find((p) => p.id === presetId);
  const needsThreshold =
    presetId === "position-count" ||
    presetId === "portfolio-value" ||
    presetId === "unrealized-loss";

  const activeRulesBody = (
    <>
      {loading ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          Loading rules…
        </p>
      ) : rules.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No rules yet. Add a preset to get notified on portfolio changes.
        </p>
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border/80 bg-card/40 p-4"
            >
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{rule.name}</p>
                  <Badge variant="outline" className="text-[10px]">
                    {rule.id}
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">
                  {String(rule.action?.message ?? rule.action?.type ?? "notify")}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={rule.enabled}
                    onCheckedChange={(checked) =>
                      void handleToggle(rule, checked)
                    }
                  />
                  <span className="text-xs text-muted-foreground">
                    {rule.enabled ? "On" : "Off"}
                  </span>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => void handleDelete(rule)}
                  aria-label="Delete rule"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );

  const eventsBody = (
    <ScrollArea className="h-[320px]">
      <div className="space-y-2 p-4">
        {events.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No rule events yet. Rules run automatically on each poll.
          </p>
        ) : (
          events.map((event) => (
            <div
              key={event.id}
              className="rounded-md border border-border/60 px-3 py-2 text-sm"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{event.rule_id}</span>
                <span className="text-xs text-muted-foreground">
                  {new Date(event.created_at).toLocaleString()}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {event.event_type}
                {typeof event.payload?.action === "object" &&
                event.payload.action &&
                "message" in (event.payload.action as object)
                  ? ` · ${String((event.payload.action as { message?: string }).message)}`
                  : ""}
              </p>
            </div>
          ))
        )}
      </div>
    </ScrollArea>
  );

  const addDialog = (
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add rule from preset</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Preset</Label>
            <Select
              value={presetId}
              onValueChange={(v) => v && setPresetId(v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RULE_PRESETS.map((preset) => (
                  <SelectItem key={preset.id} value={preset.id}>
                    {preset.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedPreset ? (
              <p className="text-xs text-muted-foreground">
                {selectedPreset.description}
              </p>
            ) : null}
          </div>
          {needsThreshold ? (
            <div className="space-y-2">
              <Label htmlFor="rule-threshold">Threshold</Label>
              <Input
                id="rule-threshold"
                type="number"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
              />
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => setDialogOpen(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => void handleCreate()}
            disabled={saving}
          >
            {saving ? "Creating…" : "Create rule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );

  if (!showCards) {
    return (
      <div className="space-y-6">
        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm font-medium">Active rules</p>
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" />
              Add rule
            </Button>
          </div>
          {activeRulesBody}
        </div>
        <div className="space-y-3">
          <p className="text-sm font-medium">Recent triggers</p>
          <div className="rounded-lg border border-border/80">{eventsBody}</div>
        </div>
        {addDialog}
      </div>
    );
  }

  return (
    <>
      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 border-b">
          <CardTitle>Active rules</CardTitle>
          <Button size="sm" onClick={() => setDialogOpen(true)}>
            <Plus className="size-4" />
            Add rule
          </Button>
        </CardHeader>
        <CardContent className="pt-4">{activeRulesBody}</CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b">
          <CardTitle>Recent triggers</CardTitle>
        </CardHeader>
        <CardContent className="p-0">{eventsBody}</CardContent>
      </Card>

      {addDialog}
    </>
  );
}
