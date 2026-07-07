"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useAui, useAuiState } from "@assistant-ui/react";

import { TerminalTranscript } from "@/components/workspace/terminal/terminal-transcript";

/**
 * Chat widget — a grid-mounted view of the SAME shared assistant-ui runtime the
 * terminal drawer renders. Both show the active thread in lockstep (switching a
 * drawer tab switches this too), because they are two renderers on one runtime
 * (now provided by AppShell). Reuses the flat mono TerminalTranscript for the
 * scrollback plus a minimal prompt that appends to the active thread. No
 * internal header — the grid's WidgetHeader owns all chrome (founder rule).
 */
export function ChatWidget() {
  const aui = useAui();
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the prompt textarea with content (capped ~6 lines).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  const submit = useCallback(() => {
    const text = value.trim();
    if (!text) return;
    setValue("");
    aui.thread().append(text);
  }, [aui, value]);

  const onSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      submit();
    },
    [submit],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submit();
        return;
      }
      // While streaming, Esc interrupts the run (Claude Code semantics).
      if (event.key === "Escape" && isRunning) {
        event.preventDefault();
        aui.thread().cancelRun();
      }
    },
    [aui, isRunning, submit],
  );

  return (
    <div
      className="flex h-full min-h-0 flex-col text-[length:var(--fs-terminal)]"
      style={{ fontFamily: "var(--font-terminal)" }}
    >
      <div className="min-h-0 flex-1 overflow-hidden">
        <TerminalTranscript />
      </div>
      <form
        onSubmit={onSubmit}
        className="flex shrink-0 items-start gap-2 border-t border-[var(--border)] px-3 py-2"
      >
        <span className="shrink-0 self-start leading-snug text-[var(--c-command)]">
          ›
        </span>
        <textarea
          ref={inputRef}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          spellCheck={false}
          autoComplete="off"
          aria-label="Chat message"
          placeholder="Ask the agent…"
          className="terminal-caret max-h-40 min-w-0 flex-1 resize-none bg-transparent leading-snug text-foreground outline-none placeholder:text-[var(--fg-faint)]"
        />
      </form>
    </div>
  );
}
