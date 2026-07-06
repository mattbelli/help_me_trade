"use client";

import { useState } from "react";
import {
  AlertCircleIcon,
  CheckIcon,
  ChevronRightIcon,
  LoaderIcon,
  XCircleIcon,
} from "lucide-react";
import {
  useToolCallElapsed,
  type ToolCallMessagePartComponent,
  type ToolCallMessagePartStatus,
} from "@assistant-ui/react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ToolFallbackApproval } from "@/components/assistant-ui/tool-fallback";
import { cn } from "@/lib/utils";

/**
 * T2 terminal tool row — the perceived-speed core (design §4). One flat
 * collapsed line per tool call: `⚙ name arg-summary · duration · status`,
 * spinner while running, click/Space expands the args/result. Derived from
 * `tool-fallback.tsx` (same collapsible + elapsed + status semantics; the
 * bubble tool block stays untouched for mobile).
 */

type ToolStatusType = ToolCallMessagePartStatus["type"];

const statusIconMap: Record<ToolStatusType, React.ElementType> = {
  running: LoaderIcon,
  complete: CheckIcon,
  incomplete: XCircleIcon,
  "requires-action": AlertCircleIcon,
};

function formatDuration(ms: number): string {
  if (ms < 1000) return "<1s";
  const seconds = ms / 1000;
  if (seconds < 10) return `${(Math.floor(seconds * 10) / 10).toFixed(1)}s`;
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}

/** A compact one-line summary of the call's args (first symbol-ish value). */
function summarizeArgs(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const record = args as Record<string, unknown>;
  for (const key of ["symbol", "ticker", "query", "preset", "scope", "command", "name"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, 48);
    if (typeof value === "number") return String(value);
  }
  const first = Object.values(record).find(
    (v) => typeof v === "string" || typeof v === "number",
  );
  return first != null ? String(first).slice(0, 48) : "";
}

export const TerminalToolRow: ToolCallMessagePartComponent = ({
  toolName,
  args,
  argsText,
  result,
  status,
  addResult,
  resume,
  interrupt,
  approval,
  respondToApproval,
}) => {
  const isRequiresAction = status?.type === "requires-action";
  const [open, setOpen] = useState(isRequiresAction);
  const [prevRequiresAction, setPrevRequiresAction] = useState(isRequiresAction);
  if (isRequiresAction !== prevRequiresAction) {
    setPrevRequiresAction(isRequiresAction);
    if (isRequiresAction) setOpen(true);
  }

  const elapsedMs = useToolCallElapsed();
  const statusType = status?.type ?? "complete";
  const isRunning = statusType === "running";
  const isError = statusType === "incomplete";
  const StatusIcon = statusIconMap[statusType];
  const summary = summarizeArgs(args);

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="w-full text-[length:var(--fs-body)]"
    >
      <CollapsibleTrigger className="group/row flex w-full items-center gap-2 py-0.5 text-left text-[var(--fg-faint)] transition-colors hover:text-[var(--fg-dim)]">
        <ChevronRightIcon className="size-3 shrink-0 transition-transform duration-150 group-data-[state=open]/row:rotate-90 motion-reduce:transition-none" />
        <span aria-hidden>⚙</span>
        <span className="shrink-0 text-[var(--fg-dim)]">{toolName}</span>
        {summary ? (
          <span className="min-w-0 truncate text-[var(--fg-faint)]">{summary}</span>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {elapsedMs !== undefined ? (
            <span className="tabular-nums">· {formatDuration(elapsedMs)}</span>
          ) : null}
          <StatusIcon
            className={cn(
              "size-3.5",
              isRunning && "animate-spin motion-reduce:animate-none",
              statusType === "complete" && "text-[var(--c-accent)]",
              isError && "text-[var(--c-danger)]",
            )}
          />
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down">
        <div className="flex flex-col gap-1 py-1 ps-5 text-[length:var(--fs-body)] text-[var(--fg-faint)]">
          {argsText ? (
            <div className="whitespace-pre-wrap break-words">
              <span className="text-[var(--fg-dim)]">├ in </span>
              {argsText}
            </div>
          ) : null}
          {result !== undefined && !isError ? (
            <div className="whitespace-pre-wrap break-words">
              <span className="text-[var(--fg-dim)]">└ out </span>
              {typeof result === "string" ? result : JSON.stringify(result)}
            </div>
          ) : null}
          {isError && status?.type === "incomplete" && status.error ? (
            <div className="whitespace-pre-wrap break-words text-[var(--c-danger)]">
              └ error{" "}
              {typeof status.error === "string"
                ? status.error
                : JSON.stringify(status.error)}
            </div>
          ) : null}
          {isRequiresAction ? (
            <ToolFallbackApproval
              addResult={addResult}
              resume={resume}
              interrupt={interrupt}
              approval={approval}
              respondToApproval={respondToApproval}
            />
          ) : null}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
};
