"use client";

import { useCallback, useEffect, useState } from "react";
import { Wrench } from "lucide-react";

import { ChatModelSelect } from "@/components/agents/chat-model-select";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { API_BASE, apiFetch } from "@/lib/api";
import {
  CHAT_TOOL_OPTIONS,
  type ChatToolId,
} from "@/lib/chat-toolbar-config";
import { useChatSettings } from "@/lib/chat-settings-context";
import { subscribeSettingsSaved } from "@/lib/settings-events";
import { cn } from "@/lib/utils";

export function TradingComposerControls({ className }: { className?: string }) {
  const { settings, setSettings } = useChatSettings();
  const [toolsOpen, setToolsOpen] = useState(false);

  const syncDefaultLlm = useCallback(() => {
    apiFetch(`${API_BASE}/api/ai/providers`, { cache: "no-store" })
      .then((res) => res.json())
      .then((data) => {
        const block = data.default_llm;
        const provider = block?.provider ?? "";
        const model = block?.model ?? "";
        if (!provider || provider === "mock") return;
        setSettings((prev) =>
          prev.provider === provider && prev.model === model
            ? prev
            : { ...prev, provider, model },
        );
      })
      .catch(() => {});
  }, [setSettings]);

  useEffect(() => {
    syncDefaultLlm();
    return subscribeSettingsSaved(syncDefaultLlm);
  }, [syncDefaultLlm]);

  function toggleTool(id: ChatToolId, checked: boolean) {
    setSettings((prev) => {
      const next = new Set(prev.enabledTools);
      if (checked) next.add(id);
      else next.delete(id);
      return { ...prev, enabledTools: [...next] };
    });
  }

  const enabledCount = settings.enabledTools.length;

  return (
    <div
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-1.5 ps-0.5",
        className,
      )}
    >
      <ChatModelSelect
        key={settings.provider}
        provider={settings.provider}
        value={settings.model}
        preferredModel={settings.model}
        onChange={(model) => setSettings((prev) => ({ ...prev, model }))}
        className="shrink-0"
      />

      <DropdownMenu open={toolsOpen} onOpenChange={setToolsOpen}>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 gap-1 px-2 text-xs font-normal"
            />
          }
        >
          <Wrench className="size-3.5" />
          <span className="hidden @md:inline">Tools</span>
          <span className="text-muted-foreground">({enabledCount})</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-72">
          <DropdownMenuGroup>
            <DropdownMenuLabel className="text-xs font-medium">
              Enabled tools
            </DropdownMenuLabel>
            <p className="text-muted-foreground px-2 pb-1 text-[11px] leading-snug">
              The model can only call tools you enable here.
            </p>
            {CHAT_TOOL_OPTIONS.map((tool) => (
              <DropdownMenuCheckboxItem
                key={tool.id}
                checked={settings.enabledTools.includes(tool.id)}
                onCheckedChange={(checked) =>
                  toggleTool(tool.id, checked === true)
                }
                className="items-start py-2"
                onSelect={(e) => e.preventDefault()}
              >
                <span className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium">{tool.label}</span>
                  <span className="text-muted-foreground text-[11px] leading-snug font-normal">
                    {tool.description}
                  </span>
                </span>
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel className="text-xs font-medium">
              Context
            </DropdownMenuLabel>
            <DropdownMenuCheckboxItem
              checked={settings.autoInjectMemories}
              onCheckedChange={(checked) =>
                setSettings((prev) => ({
                  ...prev,
                  autoInjectMemories: checked === true,
                }))
              }
              className="items-start py-2"
              onSelect={(e) => e.preventDefault()}
            >
              <span className="flex flex-col gap-0.5">
                <span className="text-xs font-medium">Auto-inject memories</span>
                <span className="text-muted-foreground text-[11px] leading-snug font-normal">
                  Prefetch relevant memories each turn. Otherwise use the
                  Memories tool.
                </span>
              </span>
            </DropdownMenuCheckboxItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
