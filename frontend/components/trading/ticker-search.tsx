"use client";

import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { searchSymbols } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  value: string;
  onChange: (symbol: string) => void;
  onSelect: (symbol: string) => void;
  className?: string;
};

export function TickerSearch({ value, onChange, onSelect, className }: Props) {
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
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
        })
        .catch(() => {
          setSuggestions([]);
          setOpen(false);
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
          onChange(e.target.value.toUpperCase());
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit(value || suggestions[0] || "");
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
        <ul className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-border bg-popover py-1 shadow-md">
          {suggestions.map((sym) => (
            <li key={sym}>
              <button
                type="button"
                className="w-full px-3 py-1.5 text-left text-sm hover:bg-muted"
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(sym);
                }}
              >
                {sym}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
