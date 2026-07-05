import * as React from "react"

import { cn } from "@/lib/utils"

export type TerminalFieldProps = {
  label: string
  htmlFor?: string
  hint?: string
  children: React.ReactNode
  className?: string
}

function TerminalField({
  label,
  htmlFor,
  hint,
  children,
  className,
}: TerminalFieldProps) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label
        htmlFor={htmlFor}
        className="font-mono text-[length:var(--fs-micro)] tracking-[0.08em] uppercase text-[var(--fg-faint)]"
      >
        {label}
      </label>
      {children}
      {hint ? (
        <p className="font-mono text-[length:var(--fs-table)] leading-snug text-[var(--fg-dim)]">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

export { TerminalField }
