import * as React from "react"
import { ChevronDown } from "lucide-react"

import { cn } from "@/lib/utils"

const TerminalSelect = React.forwardRef<
  HTMLSelectElement,
  React.ComponentProps<"select">
>(({ className, children, ...props }, ref) => {
  return (
    <div className="relative">
      <select
        ref={ref}
        data-slot="terminal-select"
        className={cn(
          "w-full appearance-none rounded border border-[var(--border)] bg-[var(--bg-inset)] px-1.5 py-1 pr-6 font-mono text-[length:var(--fs-body)] text-foreground outline-none transition-colors focus:border-[var(--c-accent)] disabled:opacity-50",
          className
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 size-3 -translate-y-1/2 text-[var(--fg-faint)]" />
    </div>
  )
})

TerminalSelect.displayName = "TerminalSelect"

export { TerminalSelect }
