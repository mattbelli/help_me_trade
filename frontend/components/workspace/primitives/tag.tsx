"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Semantic tones, mapped to the vars defined in app/globals.css. */
export type TagTone = "accent" | "dim" | "green" | "red" | "amber";

const TONE_CLASS: Record<TagTone, string> = {
  accent: "text-[var(--c-accent)]",
  dim: "text-[var(--fg-dim)]",
  green: "text-[var(--c-green)]",
  red: "text-[var(--c-red)]",
  amber: "text-[var(--c-amber)]",
};

/** The house categorical tag: a plain colored word. No border, no background,
 * no radius, no padding box — a chip is never the answer here. */
export function Tag({
  tone = "dim",
  uppercase,
  title,
  className,
  children,
}: {
  tone?: TagTone;
  uppercase?: boolean;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        TONE_CLASS[tone],
        uppercase && "tracking-[0.08em] uppercase",
        className,
      )}
      title={title}
    >
      {children}
    </span>
  );
}
