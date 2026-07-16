"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
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
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  createMemory,
  deleteMemory,
  listMemories,
  MEMORY_TYPE_LABELS,
  updateMemory,
  type MemoryRecord,
  type MemoryType,
} from "@/lib/memories";
import { cn } from "@/lib/utils";

const FILTER_TYPES: Array<MemoryType | "all"> = [
  "all",
  "preference",
  "lesson",
  "research",
];

type MemoryFormState = {
  type: MemoryType;
  content: string;
};

const EMPTY_FORM: MemoryFormState = {
  type: "preference",
  content: "",
};

function typeBadgeClass(type: MemoryType): string {
  if (type === "preference") return "border-[var(--c-blue)]/30 bg-[var(--c-blue)]/10 text-[var(--c-blue)]";
  if (type === "lesson") return "border-[var(--c-amber)]/30 bg-[var(--c-amber)]/10 text-[var(--c-amber)]";
  return "border-[var(--c-green)]/30 bg-[var(--c-green-bg)]/10 text-[var(--c-green)]";
}

function researchArtifactId(memory: MemoryRecord): number | null {
  if (memory.type !== "research") return null;
  const raw = memory.metadata?.artifact_id;
  const id = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function MemoriesPage() {
  const router = useRouter();
  const [filter, setFilter] = useState<MemoryType | "all">("all");
  const [query, setQuery] = useState("");
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<MemoryRecord | null>(null);
  const [form, setForm] = useState<MemoryFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await listMemories({
        type: filter === "all" ? undefined : filter,
        q: query.trim() || undefined,
        limit: 100,
      });
      setMemories(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load memories");
    } finally {
      setLoading(false);
    }
  }, [filter, query]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh();
    }, query ? 250 : 0);
    return () => clearTimeout(timer);
  }, [refresh, query]);

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setDialogOpen(true);
  }

  function openEdit(memory: MemoryRecord) {
    setEditing(memory);
    setForm({ type: memory.type, content: memory.content });
    setDialogOpen(true);
  }

  async function handleSave() {
    const content = form.content.trim();
    if (!content) return;
    setSaving(true);
    setError(null);
    try {
      if (editing) {
        await updateMemory(editing.id, { type: form.type, content });
      } else {
        await createMemory({ type: form.type, content });
      }
      setDialogOpen(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save memory");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(memory: MemoryRecord) {
    if (!window.confirm("Delete this memory?")) return;
    setError(null);
    try {
      await deleteMemory(memory.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory");
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Memories"
        subtitle="Preferences, lessons, and research notes the agent uses across chat, insights, and deep research."
      />

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 border-b">
          <CardTitle>Stored context</CardTitle>
          <Button size="sm" onClick={openCreate}>
            <Plus className="size-4" />
            Add memory
          </Button>
        </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative flex-1">
              <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search memories…"
                className="pl-8"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              {FILTER_TYPES.map((type) => (
                <Button
                  key={type}
                  type="button"
                  size="sm"
                  variant={filter === type ? "default" : "outline"}
                  onClick={() => setFilter(type)}
                >
                  {type === "all" ? "All" : MEMORY_TYPE_LABELS[type]}
                </Button>
              ))}
            </div>
          </div>

          <ScrollArea className="h-[min(60vh,640px)] pr-3">
            {loading ? (
              <p className="py-12 text-center text-sm text-muted-foreground">
                Loading memories…
              </p>
            ) : memories.length === 0 ? (
              <p className="py-12 text-center text-sm text-muted-foreground">
                No memories yet. Add preferences and lessons from here or let
                standard chat sessions extract them automatically.
              </p>
            ) : (
              <div className="space-y-3">
                {memories.map((memory) => {
                  const artifactId = researchArtifactId(memory);
                  const openResearch = artifactId
                    ? () => router.push(`/research?id=${artifactId}`)
                    : undefined;

                  return (
                  <div
                    key={memory.id}
                    className="rounded-lg border border-border/80 bg-card/40 p-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      {openResearch ? (
                        <button
                          type="button"
                          onClick={openResearch}
                          className="min-w-0 flex-1 cursor-pointer space-y-2 text-left"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge
                              variant="outline"
                              className={cn("text-[10px] uppercase", typeBadgeClass(memory.type))}
                            >
                              {MEMORY_TYPE_LABELS[memory.type]}
                            </Badge>
                            <span className="text-xs text-muted-foreground">
                              {new Date(memory.updated_at).toLocaleString()}
                            </span>
                          </div>
                          <p className="text-sm leading-relaxed whitespace-pre-wrap">
                            {memory.content}
                          </p>
                          {memory.metadata?.symbol ? (
                            <p className="text-xs text-muted-foreground">
                              Symbol: {String(memory.metadata.symbol)}
                            </p>
                          ) : null}
                        </button>
                      ) : (
                      <div className="min-w-0 flex-1 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            variant="outline"
                            className={cn("text-[10px] uppercase", typeBadgeClass(memory.type))}
                          >
                            {MEMORY_TYPE_LABELS[memory.type]}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {new Date(memory.updated_at).toLocaleString()}
                          </span>
                        </div>
                        <p className="text-sm leading-relaxed whitespace-pre-wrap">
                          {memory.content}
                        </p>
                        {memory.metadata?.symbol ? (
                          <p className="text-xs text-muted-foreground">
                            Symbol: {String(memory.metadata.symbol)}
                          </p>
                        ) : null}
                      </div>
                      )}
                      <div className="flex shrink-0 gap-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => openEdit(memory)}
                          aria-label="Edit memory"
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => void handleDelete(memory)}
                          aria-label="Delete memory"
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                );
                })}
              </div>
            )}
          </ScrollArea>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editing ? "Edit memory" : "Add memory"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Type</Label>
              <Select
                value={form.type}
                onValueChange={(v) =>
                  setForm((f) => ({ ...f, type: v as MemoryType }))
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(MEMORY_TYPE_LABELS) as MemoryType[]).map((t) => (
                    <SelectItem key={t} value={t}>
                      {MEMORY_TYPE_LABELS[t]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="memory-content">Content</Label>
              <Textarea
                id="memory-content"
                value={form.content}
                onChange={(e) =>
                  setForm((f) => ({ ...f, content: e.target.value }))
                }
                rows={5}
                placeholder="e.g. Risk 1% per trade on swing setups"
              />
            </div>
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
              onClick={() => void handleSave()}
              disabled={saving || !form.content.trim()}
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Add memory"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
