"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Lock, LockOpen, Plus } from "lucide-react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";
import { PageSwitcher } from "@/components/workspace/page-switcher";
import { useWorkspaceStore } from "@/lib/workspace/use-workspace-store";
import {
  useConsoleStore,
  type ScrollbackEntry,
} from "@/lib/workspace/use-console-store";
import {
  WORKSPACE_COMMANDS,
  useWorkspaceCommands,
} from "@/lib/workspace/use-workspace-commands";
import { WIDGET_REGISTRY } from "@/lib/workspace/widget-registry";

const SCROLLBACK_MAX_HEIGHT = "min(50vh, 24rem)";

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

function ScrollbackLine({ entry }: { entry: ScrollbackEntry }) {
  return (
    <div
      className={cn(
        "font-mono text-[12px] leading-relaxed whitespace-pre-wrap",
        entry.kind === "input" && "text-[var(--fg-dim)]",
        entry.kind === "output" && "text-[var(--fg-faint)]",
        entry.kind === "error" && "text-[var(--c-danger,#ef4444)]",
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

function CommandAutocomplete({
  filter,
  onSelect,
}: {
  filter: string;
  onSelect: (command: string) => void;
}) {
  const matches = getCommandSuggestions(filter);

  if (matches.length === 0) return null;

  return (
    <ul className="overflow-hidden py-1">
      {matches.map((cmd) => (
        <li key={cmd}>
          <button
            type="button"
            className="w-full px-3.5 py-1 text-left font-mono text-[11px] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)]/60 hover:text-foreground"
            onMouseDown={(e) => {
              e.preventDefault();
              onSelect(cmd);
            }}
          >
            <span className="text-[var(--c-command)]">{cmd}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function CommandConsole() {
  const { open, scrollback, toggle, setOpen, appendScrollback } =
    useConsoleStore();

  const { runCommand } = useWorkspaceCommands();
  const { locked, toggleLock, openPicker } = useWorkspaceStore();

  const consoleRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollbackRef = useRef<HTMLDivElement>(null);
  const [inputValue, setInputValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [cursorVisible, setCursorVisible] = useState(true);

  useEffect(() => {
    if (!focused) return;
    const id = setInterval(() => setCursorVisible((v) => !v), 530);
    return () => clearInterval(id);
  }, [focused]);

  useEffect(() => {
    if (!open || !scrollbackRef.current) return;
    scrollbackRef.current.scrollTop = scrollbackRef.current.scrollHeight;
  }, [open, scrollback]);

  const focusInput = useCallback(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const submitLine = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;

      if (text.startsWith("/")) {
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

      setOpen(true);
      appendScrollback({
        kind: "output",
        text: "chat lives in the Copilot →",
      });
    },
    [appendScrollback, runCommand, setOpen],
  );

  const onSubmit = useCallback(
    (event: FormEvent) => {
      event.preventDefault();
      const text = inputValue;
      setInputValue("");
      void submitLine(text);
    },
    [inputValue, submitLine],
  );

  const onInputKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        setOpen(false);
        inputRef.current?.blur();
      }
    },
    [setOpen],
  );

  useEffect(() => {
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.defaultPrevented || event.repeat) return;

      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        toggle();
        if (!open) focusInput();
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
        focusInput();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusInput, open, toggle]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (consoleRef.current?.contains(target)) return;
      setOpen(false);
    }

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open, setOpen]);

  const suggestions = getCommandSuggestions(inputValue);
  const showAutocomplete =
    open && focused && inputValue.startsWith("/") && suggestions.length > 0;
  const hasOverlayContent =
    open && (scrollback.length > 0 || showAutocomplete);

  const mirrorText = inputValue.length > 0 ? inputValue : "\u00a0";

  return (
    <motion.div
      ref={consoleRef}
      className="absolute right-0 bottom-0 left-0 z-50 h-[40px] border-t border-[var(--border)] bg-[var(--bg-raised)]/85 backdrop-blur-md"
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
    >
      {hasOverlayContent ? (
        <div className="absolute bottom-full left-4 mb-2 w-max max-w-[min(40rem,calc(100%-2rem))] overflow-hidden rounded-none border border-[var(--border)] bg-[var(--bg-raised)]/90 shadow-lg backdrop-blur-md">
          {open && scrollback.length > 0 ? (
            <div
              ref={scrollbackRef}
              className="overflow-y-auto px-3.5 py-2"
              style={{ maxHeight: SCROLLBACK_MAX_HEIGHT }}
            >
              <div className="flex flex-col gap-1">
                {scrollback.map((entry) => (
                  <ScrollbackLine key={entry.id} entry={entry} />
                ))}
              </div>
            </div>
          ) : null}

          {showAutocomplete ? (
            <CommandAutocomplete
              filter={inputValue}
              onSelect={(cmd) => {
                if (/^\/add\s+\S+$/i.test(cmd.trim())) {
                  setInputValue("");
                  void submitLine(cmd);
                  inputRef.current?.focus();
                  return;
                }
                setInputValue(`${cmd} `);
                inputRef.current?.focus();
              }}
            />
          ) : null}
        </div>
      ) : null}

      <form
        onSubmit={onSubmit}
        className="flex h-full items-center gap-2 px-4"
      >
        <span className="shrink-0 font-mono text-[var(--c-command)]">›</span>

        <div className="relative inline-grid min-w-[16rem] max-w-[50%] flex-1 items-center rounded-none border border-[var(--border)] bg-[var(--bg-inset)] px-2.5 py-1">
          <span
            aria-hidden
            className="invisible col-start-1 row-start-1 whitespace-pre font-mono text-[14px]"
          >
            {mirrorText}
            {focused ? "▋" : ""}
          </span>

          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => {
              const next = e.target.value;
              setInputValue(next);
              if (next.length > 0 && !open) setOpen(true);
            }}
            onFocus={() => {
              setFocused(true);
            }}
            onBlur={() => setFocused(false)}
            onKeyDown={onInputKeyDown}
            spellCheck={false}
            autoComplete="off"
            aria-label="Workspace command line"
            className="col-start-1 row-start-1 w-full min-w-0 bg-transparent font-mono text-[14px] text-foreground outline-none"
            style={{ caretColor: "transparent" }}
          />

          {focused ? (
            <span
              aria-hidden
              className="pointer-events-none col-start-1 row-start-1 flex items-center font-mono text-[14px] text-transparent"
            >
              {inputValue}
              <span
                className={cn(
                  "text-[var(--c-command)]",
                  cursorVisible ? "opacity-100" : "opacity-0",
                )}
              >
                ▋
              </span>
            </span>
          ) : null}
        </div>

        <div className="ml-auto flex items-center gap-2">
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
    </motion.div>
  );
}
