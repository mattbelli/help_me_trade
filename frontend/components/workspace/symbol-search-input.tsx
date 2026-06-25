"use client";

import {
  useEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type KeyboardEvent,
} from "react";

import { searchSymbols } from "@/lib/api";
import { cn } from "@/lib/utils";

type DragSafeProps = Pick<HTMLAttributes<HTMLElement>, "onClick" | "onPointerDown">;

type Props = {
  value: string;
  onChange: (value: string) => void;
  onCommit: (symbol?: string) => void;
  onEscape: () => void;
  inputClassName?: string;
  dragSafeControlProps?: DragSafeProps;
};

export function SymbolSearchInput({
  value,
  onChange,
  onCommit,
  onEscape,
  inputClassName,
  dragSafeControlProps,
}: Props) {
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = value.trim();
    if (q.length < 1) {
      setSuggestions([]);
      setOpen(false);
      setHighlightIndex(-1);
      return;
    }

    const id = setTimeout(() => {
      setLoading(true);
      searchSymbols(q)
        .then((results) => {
          setSuggestions(results);
          setOpen(results.length > 0);
          setHighlightIndex(-1);
        })
        .catch(() => {
          setSuggestions([]);
          setOpen(false);
          setHighlightIndex(-1);
        })
        .finally(() => setLoading(false));
    }, 200);

    return () => clearTimeout(id);
  }, [value]);

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setHighlightIndex(-1);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  function selectSuggestion(symbol: string) {
    setOpen(false);
    setHighlightIndex(-1);
    onCommit(symbol);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      if (!open || suggestions.length === 0) return;
      e.preventDefault();
      setHighlightIndex((i) => (i < suggestions.length - 1 ? i + 1 : 0));
      return;
    }
    if (e.key === "ArrowUp") {
      if (!open || suggestions.length === 0) return;
      e.preventDefault();
      setHighlightIndex((i) =>
        i > 0 ? i - 1 : suggestions.length - 1,
      );
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIndex >= 0 && suggestions[highlightIndex]) {
        selectSuggestion(suggestions[highlightIndex]);
      } else {
        onCommit();
      }
      e.currentTarget.blur();
      return;
    }
    if (e.key === "Escape") {
      onEscape();
      setOpen(false);
      setHighlightIndex(-1);
      e.currentTarget.blur();
    }
  }

  return (
    <div ref={wrapRef} className="relative">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value.toUpperCase())}
        onBlur={() => {
          setOpen(false);
          setHighlightIndex(-1);
          onCommit();
        }}
        onKeyDown={handleKeyDown}
        onFocus={() => {
          if (suggestions.length > 0) setOpen(true);
        }}
        {...dragSafeControlProps}
        className={cn(inputClassName)}
      />
      {loading ? (
        <span className="pointer-events-none absolute top-1/2 right-1 -translate-y-1/2 font-mono text-[9px] text-[var(--fg-faint)]">
          …
        </span>
      ) : null}
      {open && suggestions.length > 0 ? (
        <ul
          className="absolute left-0 top-full z-50 mt-0.5 min-w-[10rem] max-h-32 overflow-auto rounded border border-[var(--border)] bg-[var(--panel)] py-0.5 shadow-[var(--panel-shadow)]"
        >
          {suggestions.map((sym, idx) => (
            <li key={sym}>
              <button
                type="button"
                className={cn(
                  "w-full px-2 py-1 text-left font-mono text-[11px] text-foreground hover:bg-[var(--bg-hover)]",
                  idx === highlightIndex && "bg-[var(--bg-hover)]",
                )}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectSuggestion(sym);
                }}
                onMouseEnter={() => setHighlightIndex(idx)}
              >
                {sym}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
