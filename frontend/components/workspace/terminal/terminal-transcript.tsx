"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type FC,
  type ReactNode,
} from "react";
import {
  ErrorPrimitive,
  MessagePrimitive,
  groupPartByType,
  useAuiState,
} from "@assistant-ui/react";

import { Thread } from "@/components/assistant-ui/thread";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { TurnLearnedMemories } from "@/components/agents/turn-learned-memories";
import { ClickableSymbol } from "@/components/workspace/clickable-symbol";
import { useSnapshot } from "@/components/providers/snapshot-provider";
import { fetchWatchlists } from "@/lib/api";
import { usePublishSymbol } from "@/lib/workspace/use-publish-symbol";
import {
  useTerminalStore,
  type ScrollbackEntry,
} from "@/lib/workspace/use-terminal-store";
import { cn } from "@/lib/utils";

import { TerminalToolRow } from "./terminal-tool-row";
import { MemChip, memChipsEnabled } from "./mem-chip";

/**
 * T2 terminal transcript — the copilot's chat rendered flat in the drawer
 * scrollback (design §4). It is a *renderer* on the shared assistant-ui runtime
 * (the same one the dock uses), not a second brain: the terminal's own input
 * drives it via `aui.thread().append(...)`. Message skins are overridden to a
 * mono transcript (no bubbles); tool calls render as flat rows; proposal cards
 * resolve inline via their globally-registered `toolUI`. Command lines
 * interleave beneath the message that was current when they ran.
 */

// The terminal publishes clicked tickers to Channel 1 (its default chip).
const TERMINAL_CHANNEL = "1" as const;

// Text-token rewriter: `$SYM` / known bare tickers → ClickableSymbol,
// `[mem:ID]` → chip (dormant behind a flag). Conservative bare-symbol matching
// (known-set only) avoids false positives on ordinary uppercase words.
const TOKEN_RE = /\[mem:(\d+)\]|\$([A-Za-z]{1,5})(?![A-Za-z])|\b([A-Z]{1,5})\b/g;

function tokenize(
  text: string,
  known: Set<string>,
  publish: (symbol: string) => void,
  memOn: boolean,
): ReactNode {
  if (!text) return text;
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  const re = new RegExp(TOKEN_RE.source, "g");
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const [full, memId, dollarSym, bareSym] = match;
    // A bare uppercase run that isn't a known symbol stays plain text.
    if (bareSym && !known.has(bareSym)) continue;
    if (match.index > last) out.push(text.slice(last, match.index));
    if (memId) {
      out.push(memOn ? <MemChip key={key++} id={Number(memId)} /> : full);
    } else {
      const sym = (dollarSym ?? bareSym).toUpperCase();
      out.push(
        <ClickableSymbol
          key={key++}
          symbol={sym}
          linked
          onActivate={() => publish(sym)}
        />,
      );
    }
    last = match.index + full.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out.length > 0 ? out : text;
}

function useKnownSymbols(): Set<string> {
  const { snapshot } = useSnapshot();
  const [watch, setWatch] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchWatchlists()
      .then((state) => {
        if (cancelled) return;
        const active = state.lists.find((l) => l.id === state.active_id);
        setWatch(active?.symbols ?? []);
      })
      .catch(() => {
        // leave watchlist symbols empty; $-prefixed tickers still linkify
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return useMemo(() => {
    const set = new Set<string>();
    for (const p of snapshot?.positions ?? []) {
      if (p.symbol) set.add(p.symbol.toUpperCase());
    }
    for (const s of watch) set.add(s.toUpperCase());
    return set;
  }, [snapshot, watch]);
}

const TransformContext = createContext<(text: string) => ReactNode>((t) => t);

function TerminalCommandLine({
  entry,
  transform,
}: {
  entry: ScrollbackEntry;
  transform: (text: string) => ReactNode;
}) {
  // No explicit size — command lines inherit the transcript base
  // (--fs-terminal) so prompt echoes match the chat text.
  if (entry.kind === "input") {
    return (
      <div className="whitespace-pre-wrap break-words text-[var(--fg-dim)]">
        <span className="text-[var(--c-command)]">› </span>
        {entry.text}
      </div>
    );
  }
  return (
    <div
      className={cn(
        "whitespace-pre-wrap break-words",
        entry.kind === "error"
          ? "text-[var(--c-danger)]"
          : "text-[var(--fg-faint)]",
      )}
    >
      {transform(entry.text)}
    </div>
  );
}

/** Renders command lines anchored to a message id (or the top of the thread). */
function AnchoredCommands({ afterMessageId }: { afterMessageId: string | null }) {
  const scrollback = useTerminalStore((s) => s.scrollback);
  const transform = useContext(TransformContext);
  const threadId = useAuiState((s) => s.threadListItem.id);
  const entries = scrollback.filter(
    (e) =>
      e.afterMessageId === afterMessageId &&
      // Top-anchored (pre-thread) lines stay scoped to the thread they ran in;
      // message-anchored lines are already unique per (globally-unique) id.
      (afterMessageId !== null || e.threadId === null || e.threadId === threadId),
  );
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-col gap-0.5 py-0.5">
      {entries.map((e) => (
        <TerminalCommandLine key={e.id} entry={e} transform={transform} />
      ))}
    </div>
  );
}

const TerminalUserMessage: FC = () => {
  const transform = useContext(TransformContext);
  const id = useAuiState((s) => s.message.id);
  const isFirst = useAuiState((s) => s.message.index === 0);
  const text = useAuiState((s) =>
    s.message.parts
      .map((p) => (p.type === "text" ? (p as { text: string }).text : ""))
      .join(""),
  );

  return (
    <MessagePrimitive.Root
      data-role="user"
      className="fade-in animate-in py-0.5 duration-150 motion-reduce:animate-none"
    >
      {isFirst ? <AnchoredCommands afterMessageId={null} /> : null}
      <div className="flex items-start gap-2">
        <span className="shrink-0 select-none text-[var(--c-command)]">›</span>
        <div className="min-w-0 whitespace-pre-wrap break-words text-[var(--fg-dim)]">
          {transform(text)}
        </div>
      </div>
      <AnchoredCommands afterMessageId={id} />
    </MessagePrimitive.Root>
  );
};

const TerminalAssistantMessage: FC = () => {
  const transform = useContext(TransformContext);
  const id = useAuiState((s) => s.message.id);
  const isFirst = useAuiState((s) => s.message.index === 0);

  return (
    <MessagePrimitive.Root
      data-role="assistant"
      className="fade-in animate-in relative py-0.5 duration-150 motion-reduce:animate-none"
    >
      {isFirst ? <AnchoredCommands afterMessageId={null} /> : null}
      <div className="text-foreground leading-relaxed">
        <MessagePrimitive.GroupedParts
          groupBy={groupPartByType({
            reasoning: ["group-chainOfThought", "group-reasoning"],
            "tool-call": ["group-chainOfThought", "group-tool"],
            "standalone-tool-call": [],
          })}
        >
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                return <div>{children}</div>;
              case "group-tool":
                return (
                  <ToolGroupRoot variant="ghost">
                    <ToolGroupTrigger
                      count={part.indices.length}
                      active={part.status.type === "running"}
                    />
                    <ToolGroupContent>{children}</ToolGroupContent>
                  </ToolGroupRoot>
                );
              case "group-reasoning": {
                const running = part.status.type === "running";
                return (
                  <ReasoningRoot streaming={running}>
                    <ReasoningTrigger active={running} />
                    <ReasoningContent aria-busy={running}>
                      <ReasoningText>{children}</ReasoningText>
                    </ReasoningContent>
                  </ReasoningRoot>
                );
              }
              case "text":
                return <MarkdownText textTransform={transform} />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return part.toolUI ?? <TerminalToolRow {...part} />;
              case "data":
                return part.dataRendererUI;
              case "indicator":
                return (
                  <span
                    aria-label="Assistant is working"
                    className="flex items-center gap-[5px] py-[3px]"
                  >
                    <span className="h-[6px] w-[6px] animate-[breathe_1.14s_ease-in-out_0s_infinite] rounded-full bg-[var(--fg-dim)] motion-reduce:animate-none" />
                    <span className="h-[6px] w-[6px] animate-[breathe_1.14s_ease-in-out_160ms_infinite] rounded-full bg-[var(--fg-dim)] motion-reduce:animate-none" />
                    <span className="h-[6px] w-[6px] animate-[breathe_1.14s_ease-in-out_320ms_infinite] rounded-full bg-[var(--fg-dim)] motion-reduce:animate-none" />
                  </span>
                );
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessagePrimitive.Error>
          <ErrorPrimitive.Root className="border-destructive bg-destructive/10 text-destructive mt-2 rounded-md border p-2 text-[length:var(--fs-body)]">
            <ErrorPrimitive.Message className="line-clamp-3" />
          </ErrorPrimitive.Root>
        </MessagePrimitive.Error>
      </div>
      <TurnLearnedMemories />
      <AnchoredCommands afterMessageId={id} />
    </MessagePrimitive.Root>
  );
};

const TerminalWelcome: FC = () => (
  <div className="flex flex-col gap-2 py-1">
    <AnchoredCommands afterMessageId={null} />
    <div className="text-[length:var(--fs-body)] text-[var(--fg-faint)]">
      Type <span className="text-[var(--c-command)]">/</span> for commands ·{" "}
      <span className="text-[var(--c-command)]">/help</span> to list them · or
      just ask the agent
    </div>
  </div>
);

export function TerminalTranscript() {
  const known = useKnownSymbols();
  const { publish } = usePublishSymbol(TERMINAL_CHANNEL);
  // Dormant by default (flag off) → tokens render as plain text; read once.
  const [memOn] = useState(memChipsEnabled);

  const transform = useCallback(
    (text: string) => tokenize(text, known, publish, memOn),
    [known, publish, memOn],
  );

  return (
    <TransformContext.Provider value={transform}>
      <div className="h-full min-h-0 text-[length:var(--fs-terminal)] [font-synthesis-weight:none] [&_.aui-thread-root]:bg-transparent">
        <Thread
          messagesOnly
          fullWidth
          turnAnchor="bottom"
          components={{
            UserMessage: TerminalUserMessage,
            AssistantMessage: TerminalAssistantMessage,
            Welcome: TerminalWelcome,
          }}
        />
      </div>
    </TransformContext.Provider>
  );
}
