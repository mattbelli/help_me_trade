"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { ChevronDown, Lock, LockOpen, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { PageSwitcher } from "@/components/workspace/page-switcher";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import { useCopilotStore } from "@/lib/workspace/use-copilot-store";
import {
  TERMINAL_COLLAPSED_HEIGHT,
  useTerminalStore,
  type ScrollbackEntry,
} from "@/lib/workspace/use-terminal-store";
import {
  WORKSPACE_COMMANDS,
  useWorkspaceCommands,
} from "@/lib/workspace/use-workspace-commands";
import { WIDGET_REGISTRY } from "@/lib/workspace/widget-registry";
import { isPerfDebug, markToPaint, usePerfStore } from "@/lib/workspace/perf-marks";

const STRIP = TERMINAL_COLLAPSED_HEIGHT;

function getCommandSuggestions(input: string): string[] {
  const trimmed = input.trim();
  const lower = trimmed.toLowerCase();

  const addMatch = /^\/add(?:\s+(.*))?$/i.exec(trimmed);
  if (addMatch) {
    const partial = (addMatch[1] ?? "").trim().toLowerCase();
    return WIDGET_REGISTRY.filter(
      (entry) =>
        partial === "" ||
        entry.id.toLowerCase().includes(partial) ||
        entry.title.toLowerCase().includes(partial),
    ).map((entry) => `/add ${entry.id}`);
  }

  if (!trimmed.startsWith("/")) return [];

  return WORKSPACE_COMMANDS.filter((cmd) => cmd.startsWith(lower)).map(
    (cmd) => cmd,
  );
}

function isTypingTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT"
  );
}

/** A full `/add SYM`-style line is runnable immediately; a bare command needs args. */
function isRunnableSuggestion(cmd: string): boolean {
  return /^\/add\s+\S+$/i.test(cmd.trim());
}

function ScrollbackLine({ entry }: { entry: ScrollbackEntry }) {
  return (
    <div
      className={cn(
        "text-[length:var(--fs-title)] leading-relaxed whitespace-pre-wrap",
        entry.kind === "input" && "text-[var(--fg-dim)]",
        entry.kind === "output" && "text-[var(--fg-faint)]",
        entry.kind === "error" && "text-[var(--c-danger)]",
        entry.kind === "sent" && "text-[var(--c-accent)]",
      )}
    >
      {entry.kind === "input" ? (
        <>
          <span className="text-[var(--c-command)]">› </span>
          {entry.text}
        </>
      ) : (
        entry.text
      )}
    </div>
  );
}

export function Terminal() {
  const {
    open,
    height,
    leftFrac,
    widthFrac,
    scrollback,
    history,
    hydrate,
    setOpen,
    setHeight,
    setLayout,
    appendScrollback,
    pushHistory,
  } = useTerminalStore();

  const { runCommand } = useWorkspaceCommands();
  const { locked, toggleLock, openPicker } = useWorkspaceStore();
  const openCopilot = useCopilotStore((s) => s.setOpen);
  const perfEnabled = usePerfStore((s) => s.enabled);
  const perfLast = usePerfStore((s) => s.last);
  const setPerfEnabled = usePerfStore((s) => s.setEnabled);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollbackRef = useRef<HTMLDivElement>(null);
  const [inputValue, setInputValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [selIndex, setSelIndex] = useState(-1);
  const historyIndexRef = useRef<number | null>(null);

  const suggestions = getCommandSuggestions(inputValue);
  const showAutocomplete =
    focused && inputValue.trim().startsWith("/") && suggestions.length > 0;

  // Hydrate persisted height/history + perf flag once.
  useEffect(() => {
    hydrate();
    setPerfEnabled(isPerfDebug());
  }, [hydrate, setPerfEnabled]);

  // Keep the scrollback pinned to the newest line.
  useEffect(() => {
    if (!open || !scrollbackRef.current) return;
    scrollbackRef.current.scrollTop = scrollbackRef.current.scrollHeight;
  }, [open, scrollback]);

  // Auto-grow the prompt textarea with content (capped ~6 lines).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [inputValue, open]);

  const focusInput = useCallback(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const summon = useCallback(
    (next: boolean) => {
      if (next) markToPaint("summon");
      setOpen(next);
      if (next) focusInput();
    },
    [setOpen, focusInput],
  );

  // Quake-style click-away: the grid above stays live, so a click on a widget
  // both acts on it and drops the drawer. Listener doesn't preventDefault.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: globalThis.PointerEvent) => {
      const root = wrapperRef.current;
      if (root && e.target instanceof Node && !root.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, setOpen]);

  const submitLine = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;

      pushHistory(text);
      historyIndexRef.current = null;

      if (text.startsWith("/")) {
        markToPaint("echo");
        appendScrollback({ kind: "input", text });
        setOpen(true);

        const result = await runCommand(text);
        if (result.output) {
          appendScrollback({ kind: "output", text: result.output });
        }
        if (result.error) {
          appendScrollback({ kind: "error", text: result.error });
        }
        return;
      }

      // T1: chat still lives in the Copilot dock. Plain text routes there and
      // leaves a breadcrumb in the scrollback (full chat lands in T2).
      appendScrollback({ kind: "input", text });
      appendScrollback({
        kind: "output",
        text: "chat arrives in T2 — routing to the Copilot (⌘J) …",
      });
      setOpen(true);
      openCopilot(true);
    },
    [appendScrollback, openCopilot, pushHistory, runCommand, setOpen],
  );

  const completeSuggestion = useCallback(
    (cmd: string) => {
      if (isRunnableSuggestion(cmd)) {
        setInputValue("");
        void submitLine(cmd);
        inputRef.current?.focus();
        return;
      }
      setInputValue(`${cmd} `);
      setSelIndex(-1);
      inputRef.current?.focus();
    },
    [submitLine],
  );

  const onSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      // If a suggestion is highlighted, Enter completes/runs it instead.
      if (showAutocomplete && selIndex >= 0 && suggestions[selIndex]) {
        completeSuggestion(suggestions[selIndex]);
        return;
      }
      const text = inputValue;
      setInputValue("");
      void submitLine(text);
    },
    [completeSuggestion, inputValue, selIndex, showAutocomplete, submitLine, suggestions],
  );

  const recallHistory = useCallback(
    (dir: -1 | 1) => {
      if (history.length === 0) return;
      const prev = historyIndexRef.current;
      let next: number;
      if (prev === null) {
        // First ↑ jumps to the most recent; first ↓ does nothing.
        if (dir === 1) return;
        next = history.length - 1;
      } else {
        next = prev + dir;
      }
      if (next < 0) next = 0;
      if (next >= history.length) {
        historyIndexRef.current = null;
        setInputValue("");
        return;
      }
      historyIndexRef.current = next;
      setInputValue(history[next]);
    },
    [history],
  );

  const onInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (showAutocomplete && selIndex >= 0 && suggestions[selIndex]) {
          completeSuggestion(suggestions[selIndex]);
          return;
        }
        const text = inputValue;
        setInputValue("");
        void submitLine(text);
        return;
      }

      if (showAutocomplete) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setSelIndex((i) => (i + 1) % suggestions.length);
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setSelIndex((i) => (i <= 0 ? suggestions.length - 1 : i - 1));
          return;
        }
        if (event.key === "Tab") {
          event.preventDefault();
          completeSuggestion(suggestions[selIndex >= 0 ? selIndex : 0]);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          setSelIndex(-1);
          setInputValue("");
          return;
        }
        // Enter falls through to the form submit (handles the highlight case).
        return;
      }

      if (event.key === "ArrowUp" && !inputValue.includes("\n")) {
        event.preventDefault();
        recallHistory(-1);
        return;
      }
      if (event.key === "ArrowDown" && !inputValue.includes("\n")) {
        event.preventDefault();
        recallHistory(1);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        inputRef.current?.blur();
      }
    },
    [completeSuggestion, inputValue, recallHistory, selIndex, setOpen, showAutocomplete, submitLine, suggestions],
  );

  // ⌘K / Ctrl+K summon ⇄ dismiss; `/` on a non-typing target focuses the input.
  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) {
          setOpen(false);
          inputRef.current?.blur();
        } else {
          summon(true);
        }
        return;
      }

      if (
        event.key === "/" &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !isTypingTarget(event.target)
      ) {
        event.preventDefault();
        summon(true);
        setInputValue("/");
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusInput, open, setOpen, summon]);

  // Top-edge drag resize (25–85svh, persisted). Drag up grows the drawer.
  const onResizeStart = useCallback(
    (event: ReactPointerEvent) => {
      event.preventDefault();
      const startY = event.clientY;
      const startHeight = height;

      function onMove(e: PointerEvent) {
        setHeight(startHeight + (startY - e.clientY));
      }
      function onUp() {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      }
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [height, setHeight],
  );

  // The prompt (› + input + block cursor) renders in exactly ONE place:
  // inside the drawer above its bottom edge when open (Claude Code-style —
  // you type where you read), or in the collapsed strip when closed.
  const promptRow = (
    <>
      <span className="shrink-0 self-start pt-px text-[var(--c-command)]">›</span>
      <textarea
        ref={inputRef}
        rows={1}
        value={inputValue}
        onChange={(e) => {
          setInputValue(e.target.value);
          historyIndexRef.current = null;
          setSelIndex(-1);
        }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        onKeyDown={onInputKeyDown}
        spellCheck={false}
        autoComplete="off"
        aria-label="Terminal command line"
        className="terminal-caret max-h-40 min-w-0 flex-1 resize-none bg-transparent text-[length:var(--fs-emph)] leading-snug text-foreground outline-none"
      />
    </>
  );

  const suggestionsList = (
    <ul className="overflow-hidden rounded-none border border-[var(--border)] bg-[var(--bg-raised)]/95 py-1 shadow-lg backdrop-blur-md">
      {suggestions.map((cmd, i) => (
        <li key={cmd}>
          <button
            type="button"
            className={cn(
              "w-full px-3.5 py-1 text-left text-[length:var(--fs-body)] transition-colors",
              i === selIndex
                ? "bg-[var(--bg-hover)]/60 text-foreground"
                : "text-[var(--fg-dim)] hover:bg-[var(--bg-hover)]/40 hover:text-foreground",
            )}
            onMouseEnter={() => setSelIndex(i)}
            onMouseDown={(e) => {
              e.preventDefault();
              completeSuggestion(cmd);
            }}
          >
            <span className="text-[var(--c-command)]">{cmd}</span>
          </button>
        </li>
      ))}
    </ul>
  );

  const bodyHeight = open ? Math.max(0, height - STRIP) : 0;
  const partialWidth = widthFrac < 0.999;

  // Horizontal layout: drag the header to slide the drawer (snaps to left /
  // center / right), drag either side edge to resize. Fractions persisted.
  const beginHorizDrag = useCallback(
    (mode: "move" | "left" | "right") => (event: ReactPointerEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const wrap = wrapperRef.current;
      if (!wrap) return;
      const W = wrap.clientWidth;
      const SNAP = 24;
      const startX = event.clientX;
      const startLeft = leftFrac * W;
      const startWidth = widthFrac * W;

      function onMove(e: PointerEvent) {
        const dx = e.clientX - startX;
        let left = startLeft;
        let width = startWidth;
        if (mode === "move") {
          left = startLeft + dx;
          if (Math.abs(left) < SNAP) left = 0;
          else if (Math.abs(left + width - W) < SNAP) left = W - width;
          else if (Math.abs(left + width / 2 - W / 2) < SNAP)
            left = (W - width) / 2;
        } else if (mode === "left") {
          left = startLeft + dx;
          width = startWidth - dx;
          if (Math.abs(left) < SNAP) {
            width += left;
            left = 0;
          }
        } else {
          width = startWidth + dx;
          if (Math.abs(startLeft + width - W) < SNAP) width = W - startLeft;
        }
        setLayout(left / W, width / W);
      }
      function onUp() {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      }
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [leftFrac, widthFrac, setLayout],
  );

  return (
    <div
      ref={wrapperRef}
      className="absolute inset-x-0 bottom-0 z-50"
      style={{ fontFamily: "var(--font-terminal)" }}
    >
      {/* Drawer body — always mounted, clipped to 0 when collapsed (no remount). */}
      <div
        className={cn(
          "relative overflow-hidden bg-[var(--bg-raised)]/90 backdrop-blur-md",
          open && "border-t border-[var(--border)] shadow-lg",
          open && partialWidth && "border-x",
        )}
        style={{
          height: bodyHeight,
          width: `${widthFrac * 100}%`,
          marginLeft: `${leftFrac * 100}%`,
          transition: "height 120ms ease-out",
        }}
      >
        {/* Side edges: drag to resize width from either side. */}
        {open ? (
          <>
            <div
              onPointerDown={beginHorizDrag("left")}
              className="absolute inset-y-0 left-0 z-10 w-1.5 cursor-ew-resize transition-colors hover:bg-[var(--c-accent)]/40"
              aria-label="Resize terminal width (left edge)"
            />
            <div
              onPointerDown={beginHorizDrag("right")}
              className="absolute inset-y-0 right-0 z-10 w-1.5 cursor-ew-resize transition-colors hover:bg-[var(--c-accent)]/40"
              aria-label="Resize terminal width (right edge)"
            />
          </>
        ) : null}
        <div className="flex h-full flex-col" style={{ height: height - STRIP }}>
          {/* Drag handle */}
          <div
            onPointerDown={onResizeStart}
            className="group flex h-2 shrink-0 cursor-ns-resize items-center justify-center"
            aria-label="Resize terminal"
          >
            <span className="h-[2px] w-10 rounded-full bg-[var(--border-strong)] transition-colors group-hover:bg-[var(--c-accent)]" />
          </div>

          {/* Header — drag horizontally to dock the drawer left/center/right. */}
          <div
            onPointerDown={beginHorizDrag("move")}
            className="flex shrink-0 cursor-grab items-center gap-2 px-4 pb-1.5 text-[length:var(--fs-body)] text-[var(--fg-faint)] active:cursor-grabbing">
            <span className="text-[var(--fg-dim)]">main thread</span>
            <span className="text-[var(--fg-faint)]">·</span>
            <span>Ch1</span>
            <div className="ml-auto flex items-center gap-3">
              {perfEnabled && perfLast ? (
                <span className="tabular-nums text-[var(--c-accent)]">
                  ⏱ {perfLast.label} {perfLast.ms.toFixed(0)}ms
                </span>
              ) : null}
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => {
                  setOpen(false);
                  inputRef.current?.blur();
                }}
                className="rounded p-0.5 text-[var(--fg-faint)] transition-colors hover:text-foreground"
                aria-label="Collapse terminal"
              >
                <ChevronDown className="size-3.5" />
              </button>
            </div>
          </div>

          <div className="mx-4 border-t border-[var(--border)]" />

          {/* Scrollback */}
          <div
            ref={scrollbackRef}
            className="min-h-0 flex-1 overflow-y-auto px-4 py-2"
            style={{ contentVisibility: "auto" }}
          >
            {scrollback.length > 0 ? (
              <div className="flex flex-col gap-1">
                {scrollback.map((entry) => (
                  <ScrollbackLine key={entry.id} entry={entry} />
                ))}
              </div>
            ) : (
              <div className="text-[length:var(--fs-body)] text-[var(--fg-faint)]">
                Type <span className="text-[var(--c-command)]">/</span> for
                commands · <span className="text-[var(--c-command)]">/help</span>{" "}
                to list them · chat arrives in T2
              </div>
            )}
          </div>

          {/* Prompt line inside the pane — Claude Code-style: scrollback
              above, suggestions directly above the input, input at the
              bottom of the drawer wherever it's docked. */}
          <div className="shrink-0 px-3 pb-2">
            {showAutocomplete ? (
              <div className="mb-1">{suggestionsList}</div>
            ) : null}
            <form
              onSubmit={onSubmit}
              className="flex items-start gap-2 border-t border-[var(--border)] pt-2"
            >
              {open ? promptRow : null}
            </form>
          </div>
        </div>
      </div>

      {/* Autocomplete popover above the strip — collapsed state only (when
          open, suggestions render inside the drawer above the prompt). */}
      {showAutocomplete && !open ? (
        <div className="absolute inset-x-4 z-10" style={{ bottom: STRIP + 4 }}>
          {suggestionsList}
        </div>
      ) : null}

      {/* Input strip — always visible (the collapsed terminal). */}
      <form
        onSubmit={onSubmit}
        className="flex h-[40px] items-center gap-2 border-t border-[var(--border)] bg-[var(--bg-raised)]/85 px-4 backdrop-blur-md"
      >
        <button
          type="button"
          onClick={() => summon(true)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-[length:var(--fs-body)] text-[var(--fg-faint)] transition-colors hover:text-[var(--fg-dim)]"
          aria-label="Open terminal"
        >
          <span className="text-[var(--c-command)]">›</span>
          <kbd className="rounded-sm border border-[var(--border)] px-1 py-px text-[length:var(--fs-micro)]">
            ⌘K
          </kbd>
        </button>

        <div className="ml-auto flex items-center gap-2">
          <span className="rounded-sm border border-[var(--border)] px-1.5 py-0.5 text-[length:var(--fs-micro)] text-[var(--fg-faint)]">
            Ch1
          </span>
          <PageSwitcher />
          <button
            type="button"
            onClick={toggleLock}
            className={cn(
              "rounded p-1 transition-colors hover:bg-[var(--bg-hover)]",
              locked
                ? "text-[var(--c-accent)]"
                : "text-[var(--fg-faint)] hover:text-foreground",
            )}
            aria-label={
              locked ? "Unlock workspace layout" : "Lock workspace layout"
            }
          >
            {locked ? (
              <Lock className="size-3.5" />
            ) : (
              <LockOpen className="size-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={openPicker}
            className="rounded border border-dashed border-[var(--border-strong)] p-1 text-[var(--fg-dim)] transition-colors hover:border-[var(--c-accent)] hover:bg-[var(--c-accent-soft)] hover:text-foreground"
            aria-label="Add widget"
          >
            <Plus className="size-3.5" />
          </button>
        </div>
      </form>
    </div>
  );
}
