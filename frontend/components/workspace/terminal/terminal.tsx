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
import { ChevronDown, Lock, LockOpen, Menu, Plus, X } from "lucide-react";
import { useAui, useAuiState } from "@assistant-ui/react";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";
import { useChatSettings } from "@/lib/chat-settings-context";
import { PageSwitcher } from "@/components/workspace/page-switcher";
import { TerminalHistorySidebar } from "./terminal-history-sidebar";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import {
  TERMINAL_COLLAPSED_HEIGHT,
  useTerminalStore,
} from "@/lib/workspace/use-terminal-store";
import { TerminalTranscript } from "./terminal-transcript";
import {
  WORKSPACE_COMMANDS,
  useWorkspaceCommands,
} from "@/lib/workspace/use-workspace-commands";
import { WIDGET_REGISTRY } from "@/lib/workspace/widget-registry";
import { isPerfDebug, markToPaint, usePerfStore } from "@/lib/workspace/perf-marks";

const STRIP = TERMINAL_COLLAPSED_HEIGHT;
const SNAP = 24; // px magnet threshold — same feel as the horizontal dock snap.
// The last-active chat thread, so close/reopen (or a full reload) returns to the
// same conversation instead of booting into a fresh chat.
const THREAD_STORAGE_KEY = "hmt-terminal-thread";

/** Top-edge (in px, column coords) of the open drawer body for a given anchor. */
function topPxFromAnchor(v: "bottom" | "top" | number, H: number, bodyH: number) {
  if (v === "top") return 0;
  if (v === "bottom") return H - STRIP - bodyH;
  return v * H;
}

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

export function Terminal() {
  const {
    open,
    height,
    leftFrac,
    widthFrac,
    vAnchor,
    drawerLocked,
    tabIds,
    history,
    hydrate,
    setOpen,
    setHeight,
    setLayout,
    setVAnchor,
    toggleDrawerLock,
    addTab,
    removeTab,
    pruneTabs,
    appendScrollback,
    pushHistory,
  } = useTerminalStore();

  const reduce = useReducedMotion();
  // True while a pointer drag (move or resize) is active — kills the summon
  // height transition so the panel tracks the pointer 1:1 (brief §Constraints).
  const [dragActive, setDragActive] = useState(false);

  const isBottom = vAnchor === "bottom";
  const isTop = vAnchor === "top";
  const isFloat = typeof vAnchor === "number";

  const { runCommand } = useWorkspaceCommands();
  const { locked, toggleLock, openPicker } = useWorkspaceStore();
  const perfEnabled = usePerfStore((s) => s.enabled);
  const perfLast = usePerfStore((s) => s.last);
  const setPerfEnabled = usePerfStore((s) => s.setEnabled);

  // The terminal is a renderer on the shared chat runtime (dock + terminal
  // stay in sync by construction). Plain text routes here; slash never does.
  const aui = useAui();
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const activeRemoteId = useAuiState((s) => s.threadListItem.remoteId);
  const threadsLoading = useAuiState((s) => s.threads.isLoading);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const restoredThreadRef = useRef(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [inputValue, setInputValue] = useState("");
  const { settings } = useChatSettings();
  const [focused, setFocused] = useState(false);
  const [selIndex, setSelIndex] = useState(-1);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const hamburgerRef = useRef<HTMLButtonElement>(null);
  const historyIndexRef = useRef<number | null>(null);

  const suggestions = getCommandSuggestions(inputValue);
  const showAutocomplete =
    focused && inputValue.trim().startsWith("/") && suggestions.length > 0;

  // Hydrate persisted height/history + perf flag once.
  useEffect(() => {
    hydrate();
    setPerfEnabled(isPerfDebug());
  }, [hydrate, setPerfEnabled]);

  // Persist the active chat thread whenever it changes, so the terminal can
  // return to the same conversation after a close/reopen or a full reload.
  useEffect(() => {
    if (!activeRemoteId) return;
    try {
      localStorage.setItem(THREAD_STORAGE_KEY, activeRemoteId);
    } catch {
      // ignore
    }
  }, [activeRemoteId]);

  // On first load the runtime boots into a fresh main thread; once the server
  // thread list resolves, switch back to the last-active thread if it still
  // exists (deleted → stay on the default new chat). Runs exactly once.
  useEffect(() => {
    if (restoredThreadRef.current || threadsLoading) return;
    restoredThreadRef.current = true;
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(THREAD_STORAGE_KEY);
    } catch {
      // ignore
    }
    if (!saved || saved === activeRemoteId) return;
    if (!threadItems.some((t) => t.remoteId === saved)) return;
    void aui.threads().switchToThread(saved);
  }, [aui, activeRemoteId, threadItems, threadsLoading]);

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

  // ── Terminal tabs — one tab per open thread (closing a tab leaves the strip
  // but never deletes the thread; it stays in the history switcher). The open
  // set + persistence live in the terminal store; these effects keep it in sync
  // with the runtime's thread list (add-active / prune-deleted). ──────────────

  // The active thread is always a tab (opening from the history switcher, the
  // restored last-thread, or a new thread once it earns a remoteId all land here).
  useEffect(() => {
    if (activeRemoteId) addTab(activeRemoteId);
  }, [activeRemoteId, addTab]);

  // Drop tabs whose thread no longer exists server-side (deleted from history).
  // The active thread is kept even if the list hasn't caught up to a fresh id.
  useEffect(() => {
    if (threadsLoading) return;
    const existing = new Set(
      threadItems
        .map((t) => t.remoteId)
        .filter((id): id is string => Boolean(id)),
    );
    pruneTabs(existing, activeRemoteId);
  }, [threadsLoading, threadItems, activeRemoteId, pruneTabs]);

  const titleFor = useCallback(
    (id: string) =>
      threadItems.find((t) => t.remoteId === id)?.title?.trim() || "New chat",
    [threadItems],
  );

  const openNewTab = useCallback(() => {
    void aui.threads().switchToNewThread();
    setOpen(true);
    focusInput();
  }, [aui, setOpen, focusInput]);

  const selectTab = useCallback(
    (id: string) => {
      if (id !== activeRemoteId) void aui.threads().switchToThread(id);
      setOpen(true);
    },
    [aui, activeRemoteId, setOpen],
  );

  const closeTab = useCallback(
    (id: string) => {
      const idx = tabIds.indexOf(id);
      if (idx === -1) return;
      removeTab(id);
      // Closing the active tab hands focus to a neighbor, or a fresh thread if
      // the strip is now empty. The closed thread is untouched server-side.
      if (id === activeRemoteId) {
        const rest = tabIds.filter((t) => t !== id);
        const fallback = rest[idx] ?? rest[idx - 1] ?? null;
        if (fallback) void aui.threads().switchToThread(fallback);
        else void aui.threads().switchToNewThread();
      }
    },
    [aui, tabIds, activeRemoteId, removeTab],
  );

  // ── History sidebar — a quake-style slide-out over the transcript listing all
  // server threads (the data the old title dropdown showed). Transient: closed
  // whenever the drawer closes. Closing refocuses the prompt so a follow-up Esc
  // falls through to the drawer's own close handler. ──────────────────────────
  // Drop the sidebar the moment the drawer closes (render-time reset — reopening
  // the drawer never reshows a stale sidebar).
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (!open && sidebarOpen) setSidebarOpen(false);
  }

  const closeSidebar = useCallback(() => {
    setSidebarOpen(false);
    focusInput();
  }, [focusInput]);

  // Newest-first, and only threads that actually exist server-side (a remoteId).
  const sidebarThreads = threadItems
    .filter((t) => Boolean(t.remoteId))
    .map((t) => ({
      id: t.remoteId as string,
      title: t.title?.trim() || "New chat",
      lastMessageAt: t.lastMessageAt,
    }))
    .sort(
      (a, b) =>
        (b.lastMessageAt?.getTime() ?? 0) - (a.lastMessageAt?.getTime() ?? 0),
    );

  const selectFromSidebar = useCallback(
    (id: string) => {
      selectTab(id);
      closeSidebar();
    },
    [selectTab, closeSidebar],
  );

  const newChatFromSidebar = useCallback(() => {
    openNewTab();
    setSidebarOpen(false);
  }, [openNewTab]);

  const renameThread = useCallback(
    (id: string, title: string) => aui.threads().item({ id }).rename(title),
    [aui],
  );

  const deleteThread = useCallback(
    (id: string) => aui.threads().item({ id }).delete(),
    [aui],
  );

  // Quake-style click-away: the grid above stays live, so a click on a widget
  // both acts on it and drops the drawer. Listener doesn't preventDefault.
  useEffect(() => {
    if (!open || drawerLocked) return;
    const onPointerDown = (e: globalThis.PointerEvent) => {
      // Only a primary (left) click collapses. A middle-button pan or a
      // right-click anywhere must never drop the drawer (founder report).
      if (e.button !== 0) return;
      const root = wrapperRef.current;
      if (!root || !(e.target instanceof Node)) return;
      if (root.contains(e.target)) return;
      // Portaled UI summoned from the terminal (thread-rename dialog, tooltips)
      // lives outside the wrapper — don't collapse when interacting with it.
      if (
        e.target instanceof Element &&
        e.target.closest("[data-radix-portal],[role=dialog]")
      ) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, drawerLocked, setOpen]);

  const submitLine = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;

      pushHistory(text);
      historyIndexRef.current = null;

      // Slash → deterministic command; NEVER reaches the LLM. Capture the
      // interleave anchor (current thread + last message) so the transcript
      // renders these lines in context beneath that message (design §4).
      if (text.startsWith("/")) {
        markToPaint("echo");
        const messages = aui.thread().getState().messages;
        const afterMessageId = messages.at(-1)?.id ?? null;
        const threadId = aui.threadListItem().getState().id;
        appendScrollback({ kind: "input", text, threadId, afterMessageId });
        setOpen(true);

        const result = await runCommand(text);
        if (result.output) {
          appendScrollback({
            kind: "output",
            text: result.output,
            threadId,
            afterMessageId,
          });
        }
        if (result.error) {
          appendScrollback({
            kind: "error",
            text: result.error,
            threadId,
            afterMessageId,
          });
        }
        return;
      }

      // Anything else → the chat brain, streamed into the same scrollback. The
      // append renders the user line optimistically (F5) and starts the run.
      markToPaint("echo");
      setOpen(true);
      aui.thread().append(text);
    },
    [aui, appendScrollback, pushHistory, runCommand, setOpen],
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
        // While streaming, Esc interrupts the run (Claude Code semantics);
        // idle, it collapses the drawer.
        if (isRunning) {
          aui.thread().cancelRun();
          return;
        }
        setOpen(false);
        inputRef.current?.blur();
      }
    },
    [aui, completeSuggestion, inputValue, isRunning, recallHistory, selIndex, setOpen, showAutocomplete, submitLine, suggestions],
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

  // Edge drag resize (25–85svh, persisted). The handle sits on the free edge
  // (opposite the anchor): 'top' grows up with the bottom fixed (bottom dock),
  // 'bottom' grows down with the top fixed (top dock / floating).
  const beginVertResize = useCallback(
    (edge: "top" | "bottom") => (event: ReactPointerEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setDragActive(true);
      const startY = event.clientY;
      const startHeight = height;

      function onMove(e: PointerEvent) {
        const dy = e.clientY - startY;
        setHeight(edge === "top" ? startHeight - dy : startHeight + dy);
      }
      function onUp() {
        setDragActive(false);
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
      {/* Same font-size + line-height as the textarea: identical line boxes
          keep the glyph and the blinking caret on one baseline. */}
      <span className="shrink-0 self-start text-[length:var(--fs-emph)] leading-snug text-[var(--c-command)]">
        ›
      </span>
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

  // Side-edge resize: drag either vertical edge to widen the drawer. Snaps the
  // dragged edge to the container edge. Fractions persisted.
  const beginHorizDrag = useCallback(
    (mode: "left" | "right") => (event: ReactPointerEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const wrap = wrapperRef.current;
      if (!wrap) return;
      const W = wrap.clientWidth;
      const startX = event.clientX;
      const startLeft = leftFrac * W;
      const startWidth = widthFrac * W;

      function onMove(e: PointerEvent) {
        const dx = e.clientX - startX;
        let left = startLeft;
        let width = startWidth;
        if (mode === "left") {
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

  // Header drag = move the open drawer on BOTH axes with magnet snap (same
  // SNAP=24 feel). Horizontal: left / right edge. Vertical: top dock / bottom
  // dock, else free float (clamped on-screen) — no center magnets. Snap is
  // applied live during the drag, mirroring the horizontal dock feel. A numeric
  // vAnchor persisted from the old center magnet simply floats (isFloat).
  const beginMove = useCallback(
    (event: ReactPointerEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const wrap = wrapperRef.current;
      if (!wrap) return;
      const W = wrap.clientWidth;
      const H = wrap.clientHeight;
      const bodyH = Math.max(0, height - STRIP);
      const startX = event.clientX;
      const startY = event.clientY;
      const startLeft = leftFrac * W;
      const width = widthFrac * W;
      const startTop = topPxFromAnchor(vAnchor, H, bodyH);
      setDragActive(true);

      function onMove(e: PointerEvent) {
        // Horizontal — left / right edge only (no center magnet: the founder
        // wants magnets on the top, bottom and sides, not the middle).
        const dx = e.clientX - startX;
        let left = startLeft + dx;
        if (Math.abs(left) < SNAP) left = 0;
        else if (Math.abs(left + width - W) < SNAP) left = W - width;
        setLayout(left / W, widthFrac);

        // Vertical — clamp free position on-screen (above the strip), then snap
        // to the top or bottom dock only (no vertical-center magnet).
        const dy = e.clientY - startY;
        const maxTop = Math.max(0, H - STRIP - bodyH);
        const top = Math.max(0, Math.min(startTop + dy, maxTop));
        if (Math.abs(top) < SNAP) {
          setVAnchor("top");
        } else if (Math.abs(top + bodyH - (H - STRIP)) < SNAP) {
          setVAnchor("bottom");
        } else {
          setVAnchor(top / H);
        }
      }
      function onUp() {
        setDragActive(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      }
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [leftFrac, widthFrac, vAnchor, height, setLayout, setVAnchor],
  );

  // Vertical placement of the OPEN drawer body. Bottom dock sits flush above
  // the strip (grows up); top dock is anchored to the column top (grows down);
  // a numeric anchor floats at topFrac of the column.
  const vStyle = isBottom
    ? { bottom: STRIP }
    : isTop
      ? { top: 0 }
      : { top: `${(vAnchor as number) * 100}%` };

  return (
    // Full-column overlay, but transparent to pointers so the grid above stays
    // live (click-away below keys off DOM containment, not hit-testing). The
    // drawer + strip opt back in with pointer-events-auto.
    <div
      ref={wrapperRef}
      className="pointer-events-none absolute inset-0 z-50"
      style={{ fontFamily: "var(--font-terminal)" }}
    >
      {/* Drawer body — always mounted, clipped to 0 when collapsed (no remount).
          Absolutely positioned per vAnchor; the flush band mirrors top⇄bottom. */}
      <div
        className={cn(
          "pointer-events-auto absolute overflow-hidden bg-[var(--bg-raised)]/90 backdrop-blur-md",
          open && "shadow-lg",
          open && isBottom && (partialWidth ? "border-t border-x border-[var(--border)]" : "border-t border-[var(--border)]"),
          open && isTop && (partialWidth ? "border-b border-x border-[var(--border)]" : "border-b border-[var(--border)]"),
          open && isFloat && "border border-[var(--border)]",
        )}
        style={{
          ...vStyle,
          height: bodyHeight,
          width: `${widthFrac * 100}%`,
          left: `${leftFrac * 100}%`,
          transition: dragActive || reduce ? "none" : "height 120ms ease-out",
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
            {/* Bottom-edge resize handle — mirror of the top pill, shown when the
                drawer is anchored top / floating (top fixed, grows downward). */}
            {isTop || isFloat ? (
              <div
                onPointerDown={beginVertResize("bottom")}
                className="absolute inset-x-0 bottom-0 z-10 h-1.5 cursor-ns-resize transition-colors hover:bg-[var(--c-accent)]/40"
                aria-label="Resize terminal (bottom edge)"
              />
            ) : null}
          </>
        ) : null}
        <div className="flex h-full flex-col" style={{ height: height - STRIP }}>
          {/* Top pill resize handle — bottom-docked drawer grows upward from
              here. Hidden when anchored top / floating (they resize from the
              bottom edge instead); the header below stays the move handle. */}
          {isBottom ? (
            <div
              onPointerDown={beginVertResize("top")}
              className="group flex h-2 shrink-0 cursor-ns-resize items-center justify-center"
              aria-label="Resize terminal"
            >
              <span className="h-[2px] w-10 rounded-full bg-[var(--border-strong)] transition-colors group-hover:bg-[var(--c-accent)]" />
            </div>
          ) : (
            <div className="h-2 shrink-0" />
          )}

          {/* Header — drag to move the drawer on both axes (dock or float). */}
          <div
            onPointerDown={beginMove}
            className="flex shrink-0 cursor-grab items-center gap-2 px-4 pb-1.5 text-[length:var(--fs-body)] text-[var(--fg-faint)] active:cursor-grabbing">
            {/* Hamburger — toggles the chat-history sidebar (slides over the
                transcript). The active tab is the title; the sidebar is history. */}
            <button
              ref={hamburgerRef}
              type="button"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => setSidebarOpen((v) => !v)}
              className={cn(
                "shrink-0 rounded-sm p-0.5 transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground",
                sidebarOpen ? "text-foreground" : "text-[var(--fg-faint)]",
              )}
              aria-label="Chat history"
              aria-expanded={sidebarOpen}
            >
              <Menu className="size-3.5" />
            </button>
            {/* Tab strip — one quiet tab per open thread; overflows to a
                horizontal scroll when the drawer is narrow. */}
            <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
              {tabIds.map((id) => {
                const active = id === activeRemoteId;
                const label = titleFor(id);
                return (
                  <span
                    key={id}
                    onPointerDown={(e) => e.stopPropagation()}
                    className={cn(
                      "group/tab flex shrink-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[length:var(--fs-micro)] transition-colors",
                      active
                        ? "border-[var(--border-strong)] bg-[var(--bg-hover)]/60 text-foreground"
                        : "border-transparent text-[var(--fg-faint)] hover:bg-[var(--bg-hover)]/40 hover:text-[var(--fg-dim)]",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => selectTab(id)}
                      onAuxClick={(e) => {
                        if (e.button === 1) {
                          e.preventDefault();
                          closeTab(id);
                        }
                      }}
                      className="max-w-[9rem] truncate text-left"
                      title={label}
                    >
                      {label}
                    </button>
                    <button
                      type="button"
                      onClick={() => closeTab(id)}
                      aria-label="Close tab"
                      className={cn(
                        "shrink-0 rounded-sm transition-opacity hover:text-foreground",
                        active
                          ? "opacity-70 hover:opacity-100"
                          : "opacity-0 group-hover/tab:opacity-100",
                      )}
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                );
              })}
              {/* A brand-new thread has no remoteId yet — show it as an active,
                  non-closable tab until its first message persists it. */}
              {!activeRemoteId ? (
                <span
                  onPointerDown={(e) => e.stopPropagation()}
                  className="flex shrink-0 items-center rounded-sm border border-[var(--border-strong)] bg-[var(--bg-hover)]/60 px-1.5 py-0.5 text-[length:var(--fs-micro)] text-foreground"
                >
                  New chat
                </span>
              ) : null}
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={openNewTab}
                aria-label="New chat"
                title="New chat"
                className="shrink-0 rounded-sm p-0.5 text-[var(--fg-faint)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
              >
                <Plus className="size-3" />
              </button>
            </div>
            <div className="ml-auto flex items-center gap-3">
              {isRunning ? (
                <span
                  aria-label="Agent is working"
                  className="inline-block size-1.5 animate-[breathe_1.14s_ease-in-out_infinite] rounded-full bg-[var(--c-accent)] motion-reduce:animate-none"
                />
              ) : null}
              <span className="shrink-0">Ch1</span>
              {perfEnabled && perfLast ? (
                <span className="tabular-nums text-[var(--c-accent)]">
                  ⏱ {perfLast.label} {perfLast.ms.toFixed(0)}ms
                </span>
              ) : null}
              <button
                type="button"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={toggleDrawerLock}
                className={cn(
                  "rounded p-0.5 transition-colors",
                  drawerLocked
                    ? "text-[var(--c-accent)]"
                    : "text-[var(--fg-faint)] hover:text-foreground",
                )}
                aria-label="Keep open when clicking away"
                aria-pressed={drawerLocked}
                title="Keep open when clicking away"
              >
                {drawerLocked ? (
                  <Lock className="size-3.5" />
                ) : (
                  <LockOpen className="size-3.5" />
                )}
              </button>
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

          {/* Transcript + prompt, with the history sidebar overlaying from the
              left edge (no reflow). */}
          <div className="relative flex min-h-0 flex-1 flex-col">
            {/* Scrollback — the chat transcript (flat mono skin) with command
                lines interleaved. Always mounted; the drawer clips when collapsed
                so a running stream keeps painting and reopens live. */}
            <div className="min-h-0 flex-1 overflow-hidden">
              <TerminalTranscript />
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
                {/* Active chat model — bottom-right of the command line (muted,
                    truncates so a long provider/model key can't crowd the input). */}
                {open ? (
                  <span
                    className="shrink-0 self-end max-w-[45%] truncate text-[length:var(--fs-micro)] leading-snug text-[var(--fg-faint)]"
                    title={`Active chat model: ${settings.model || "default"}`}
                  >
                    {settings.model || "default"}
                  </span>
                ) : null}
              </form>
            </div>

            <TerminalHistorySidebar
              open={sidebarOpen}
              threads={sidebarThreads}
              activeId={activeRemoteId ?? null}
              reduce={Boolean(reduce)}
              hamburgerRef={hamburgerRef}
              onClose={closeSidebar}
              onSelectThread={selectFromSidebar}
              onNewChat={newChatFromSidebar}
              onRename={renameThread}
              onDelete={deleteThread}
            />
          </div>
        </div>
      </div>

      {/* Autocomplete popover above the strip — collapsed state only (when
          open, suggestions render inside the drawer above the prompt). */}
      {showAutocomplete && !open ? (
        <div className="pointer-events-auto absolute inset-x-4 z-10" style={{ bottom: STRIP + 4 }}>
          {suggestionsList}
        </div>
      ) : null}

      {/* Input strip — always visible at the bottom (the collapsed terminal). */}
      <form
        onSubmit={onSubmit}
        className="pointer-events-auto absolute inset-x-0 bottom-0 flex h-[40px] items-center gap-2 border-t border-[var(--border)] bg-[var(--bg-raised)]/85 px-4 backdrop-blur-md"
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
          {/* A run that started before collapse keeps streaming; glyph only. */}
          {isRunning ? (
            <span
              aria-label="Agent is working"
              className="inline-block size-1.5 animate-[breathe_1.14s_ease-in-out_infinite] rounded-full bg-[var(--c-accent)] motion-reduce:animate-none"
            />
          ) : null}
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
