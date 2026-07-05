import * as React from "react"

import { cn } from "@/lib/utils"

export type TerminalButtonProps = React.ComponentProps<"button"> & {
  variant?: "default" | "primary" | "danger"
}

const TerminalButton = React.forwardRef<HTMLButtonElement, TerminalButtonProps>(
  ({ className, variant = "default", type = "button", ...props }, ref) => {
    return (
      <button
        ref={ref}
        type={type}
        data-slot="terminal-button"
        className={cn(
          "rounded border px-2 py-0.5 font-mono text-[length:var(--fs-table)] transition-colors disabled:opacity-40",
          variant === "primary"
            ? "border-[color-mix(in_srgb,var(--c-accent)_40%,transparent)] text-[var(--c-accent)] hover:bg-[var(--c-accent-soft)]"
            : variant === "danger"
              ? "border-[color-mix(in_srgb,var(--c-red)_35%,transparent)] text-[var(--c-red)] hover:bg-[color-mix(in_srgb,var(--c-red)_12%,transparent)]"
              : "border-[var(--border)] text-[var(--fg-dim)] hover:bg-[var(--bg-hover)] hover:text-foreground",
          className
        )}
        {...props}
      />
    )
  }
)

TerminalButton.displayName = "TerminalButton"

export { TerminalButton }
