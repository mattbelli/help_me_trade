"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { motion } from "motion/react";
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  useConsoleStore,
  type ScrollbackEntry,
} from "@/lib/workspace/use-console-store";
import {
  WORKSPACE_COMMANDS,
  useWorkspaceCommands,
} from "@/lib/workspace/use-workspace-commands";

const SCROLLBACK_MAX_HEIGHT = "min(50vh, 24rem)";

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
  const matches = WORKSPACE_COMMANDS.filter((cmd) =>
    cmd.startsWith(filter.toLowerCase()),
  );

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

  const showAutocomplete = open && focused && inputValue === "/";
  const hasContentAbove =
    open && (scrollback.length > 0 || showAutocomplete);

  return (
    <motion.div
      ref={consoleRef}
      layout
      className="absolute bottom-3 left-3 z-50 flex w-[min(40rem,calc(100%-1.5rem))] max-w-[40rem] flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-raised)]/85 shadow-lg backdrop-blur-md"
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
    >
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
            setInputValue(`${cmd} `);
            inputRef.current?.focus();
          }}
        />
      ) : null}

      <form
        onSubmit={onSubmit}
        className={cn(
          "relative flex h-[46px] shrink-0 items-center gap-2 px-3.5",
          hasContentAbove && "border-t border-[var(--border)]",
        )}
      >
        <span className="shrink-0 font-mono text-[var(--c-command)]">›</span>

        <div className="relative min-w-0 flex-1">
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
            className="w-full bg-transparent font-mono text-[13px] text-foreground outline-none"
            style={{ caretColor: "transparent" }}
          />
          {focused ? (
            <span
              aria-hidden
              className="pointer-events-none absolute inset-y-0 left-0 flex items-center font-mono text-[13px] text-transparent"
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

        <span className="hidden font-mono text-[10.5px] text-[var(--fg-faint)] sm:inline">
          ⌘K
        </span>

        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          className="text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground"
          onClick={toggle}
          aria-label={open ? "Collapse console" : "Expand console"}
        >
          {open ? (
            <ChevronDownIcon className="size-4" />
          ) : (
            <ChevronUpIcon className="size-4" />
          )}
        </Button>
      </form>
    </motion.div>
  );
}
