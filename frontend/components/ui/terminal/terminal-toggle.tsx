import * as React from "react"

import { cn } from "@/lib/utils"

export type TerminalToggleProps = {
  checked: boolean
  onCheckedChange: (next: boolean) => void
  tone?: "default" | "danger" | "warn"
  disabled?: boolean
  "aria-label"?: string
  className?: string
}

function TerminalToggle({
  checked,
  onCheckedChange,
  tone = "default",
  disabled,
  "aria-label": ariaLabel,
  className,
}: TerminalToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      data-slot="terminal-toggle"
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 font-mono text-[11px] transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40",
        checked
          ? tone === "danger"
            ? "border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] text-[var(--c-red)]"
            : tone === "warn"
              ? "border-[color-mix(in_srgb,var(--c-amber)_35%,transparent)] text-[var(--c-amber)]"
              : "border-[color-mix(in_srgb,var(--c-accent)_35%,transparent)] text-[var(--c-accent)]"
          : "border-[var(--border)] text-[var(--fg-faint)]",
        className
      )}
    >
      <span>{checked ? "[x]" : "[ ]"}</span>
      {checked ? "ON" : "OFF"}
    </button>
  )
}

export { TerminalToggle }
