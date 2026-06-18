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
    <ul className="absolute bottom-full left-0 z-10 mb-1 max-h-36 w-full overflow-auto rounded-md border border-[var(--border)] bg-[var(--bg-raised)] py-1 shadow-lg">
      {matches.map((cmd) => (
        <li key={cmd}>
          <button
            type="button"
            className="w-full px-3 py-1 text-left font-mono text-[11px] text-[var(--fg-dim)] transition-colors hover:bg-[var(--bg-hover)] hover:text-foreground"
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
  const {
    open,
    height,
    scrollback,
    collapsedHeight,
    toggle,
    setOpen,
    setHeight,
    appendScrollback,
  } = useConsoleStore();

  const { runCommand } = useWorkspaceCommands();

  const consoleRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const scrollbackRef = useRef<HTMLDivElement>(null);
  const resizeRef = useRef<{ startY: number; startHeight: number } | null>(
    null,
  );
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

  const onResizePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!open) return;
      event.preventDefault();
      resizeRef.current = { startY: event.clientY, startHeight: height };
      const onMove = (moveEvent: PointerEvent) => {
        if (!resizeRef.current) return;
        const delta = resizeRef.current.startY - moveEvent.clientY;
        setHeight(resizeRef.current.startHeight + delta);
      };
      const onUp = () => {
        resizeRef.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [height, open, setHeight],
  );

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

  const panelHeight = open ? height : collapsedHeight;
  const showAutocomplete = focused && inputValue === "/";

  return (
    <motion.div
      ref={consoleRef}
      className="absolute inset-x-0 bottom-0 z-50 flex flex-col border-t border-[var(--border)] bg-[var(--bg-raised)] shadow-[0_-10px_40px_rgba(0,0,0,0.3)]"
      animate={{ height: panelHeight }}
      initial={false}
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
    >
      {open ? (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize console"
          className="absolute -top-1.5 right-0 left-0 z-10 h-2 cursor-row-resize"
          onPointerDown={onResizePointerDown}
        >
          <div className="absolute top-1 left-1/2 h-[3px] w-11 -translate-x-1/2 rounded-full bg-[var(--border-strong)]" />
        </div>
      ) : null}

      {open && scrollback.length > 0 ? (
        <div
          ref={scrollbackRef}
          className="min-h-0 flex-1 overflow-y-auto px-3.5 py-2"
        >
          <div className="flex flex-col gap-1">
            {scrollback.map((entry) => (
              <ScrollbackLine key={entry.id} entry={entry} />
            ))}
          </div>
        </div>
      ) : open ? (
        <div className="min-h-0 flex-1" />
      ) : null}

      <form
        onSubmit={onSubmit}
        className={cn(
          "relative flex h-[46px] shrink-0 items-center gap-2 px-3.5",
          open && "border-t border-[var(--border)]",
        )}
      >
        {showAutocomplete ? (
          <CommandAutocomplete
            filter={inputValue}
            onSelect={(cmd) => {
              setInputValue(`${cmd} `);
              inputRef.current?.focus();
            }}
          />
        ) : null}

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
