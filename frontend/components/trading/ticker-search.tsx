"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { searchSymbols } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  value: string;
  onChange: (symbol: string) => void;
  onSelect: (symbol: string) => void;
  className?: string;
  /** Symbols already on the active list — marked "on list" in the dropdown. */
  existing?: string[];
};

export function TickerSearch({
  value,
  onChange,
  onSelect,
  className,
  existing,
}: Props) {
  const onList = useMemo(
    () => new Set((existing ?? []).map((s) => s.toUpperCase())),
    [existing],
  );
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const userTypingRef = useRef(false);

  useEffect(() => {
    if (!userTypingRef.current) return;

    const q = value.trim();
    if (q.length < 1) {
      setSuggestions([]);
      setOpen(false);
      return;
    }

    const id = setTimeout(() => {
      setLoading(true);
      searchSymbols(q)
        .then((symbols) => {
          setSuggestions(symbols);
          setOpen(symbols.length > 0);
          setActiveIndex(-1);
        })
        .catch(() => {
          setSuggestions([]);
          setOpen(false);
          setActiveIndex(-1);
        })
        .finally(() => setLoading(false));
    }, 200);

    return () => clearTimeout(id);
  }, [value]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function commit(symbol: string) {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    userTypingRef.current = false;
    onSelect(sym);
    onChange(sym);
    setOpen(false);
    setSuggestions([]);
  }

  return (
    <div ref={wrapRef} className={cn("relative", className)}>
      <Input
        value={value}
        placeholder="Search ticker…"
        className="uppercase"
        onChange={(e) => {
          userTypingRef.current = true;
          setActiveIndex(-1);
          onChange(e.target.value.toUpperCase());
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            if (suggestions.length === 0) return;
            setOpen(true);
            setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
            return;
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, -1));
            return;
          }
          if (e.key === "Enter") {
            e.preventDefault();
            commit(
              activeIndex >= 0
                ? suggestions[activeIndex]
                : value || suggestions[0] || "",
            );
          }
          if (e.key === "Escape") setOpen(false);
        }}
        onFocus={() => {
          if (userTypingRef.current && suggestions.length > 0) setOpen(true);
        }}
        onBlur={() => {
          userTypingRef.current = false;
        }}
      />
      {loading && (
        <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-[10px] text-muted-foreground">
          …
        </span>
      )}
      {open && suggestions.length > 0 && (
        <ul
          className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-[var(--border-strong)] py-1 shadow-2xl ring-1 ring-black/50"
          style={{
            backgroundColor: "color-mix(in srgb, var(--popover) 86%, #ffffff)",
          }}
        >
          {suggestions.map((sym, i) => {
            const already = onList.has(sym.toUpperCase());
            return (
              <li key={sym}>
                <button
                  type="button"
                  className={cn(
                    "flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm hover:bg-[var(--c-accent-soft)]",
                    i === activeIndex && "bg-[var(--c-accent-soft)]",
                  )}
                  onMouseEnter={() => setActiveIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    commit(sym);
                  }}
                >
                  <span>{sym}</span>
                  {already && (
                    <span className="text-[10px] text-muted-foreground">
                      on list
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
