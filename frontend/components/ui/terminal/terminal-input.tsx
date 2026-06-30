import * as React from "react"

import { cn } from "@/lib/utils"

const TerminalInput = React.forwardRef<
  HTMLInputElement,
  React.ComponentProps<"input">
>(({ className, ...props }, ref) => {
  return (
    <input
      ref={ref}
      data-slot="terminal-input"
      className={cn(
        "w-full rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1.5 py-1 font-mono text-[11px] text-foreground outline-none transition-colors placeholder:text-[var(--fg-faint)] focus:border-[var(--c-accent)] disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
})

TerminalInput.displayName = "TerminalInput"

export { TerminalInput }
