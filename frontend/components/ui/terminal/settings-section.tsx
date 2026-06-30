import * as React from "react"

import { cn } from "@/lib/utils"

export type SettingsSectionProps = {
  title?: string
  description?: string
  variant?: "plain" | "card"
  tone?: "default" | "danger" | "warn"
  children: React.ReactNode
  className?: string
}

function SettingsSection({
  title,
  description,
  variant = "plain",
  tone = "default",
  children,
  className,
}: SettingsSectionProps) {
  const content = (
    <>
      {title ? (
        <h3 className="font-mono text-[11px] tracking-[0.1em] uppercase text-foreground">
          {title}
        </h3>
      ) : null}
      {description ? (
        <p className="font-mono text-[10px] leading-snug text-[var(--fg-dim)]">
          {description}
        </p>
      ) : null}
      {children}
    </>
  )

  if (variant === "card") {
    return (
      <section
        data-slot="settings-section"
        className={cn("space-y-3", className)}
      >
        {title || description ? (
          <div className="space-y-1">
            {title ? (
              <h3 className="font-mono text-[11px] tracking-[0.1em] uppercase text-foreground">
                {title}
              </h3>
            ) : null}
            {description ? (
              <p className="font-mono text-[10px] leading-snug text-[var(--fg-dim)]">
                {description}
              </p>
            ) : null}
          </div>
        ) : null}
        <div
          className={cn(
            "space-y-3 rounded border p-3",
            tone === "danger"
              ? "border-[color-mix(in_srgb,var(--c-red)_30%,transparent)] bg-[color-mix(in_srgb,var(--c-red)_6%,transparent)]"
              : tone === "warn"
                ? "border-[color-mix(in_srgb,var(--c-amber)_30%,transparent)] bg-[color-mix(in_srgb,var(--c-amber)_6%,transparent)]"
                : "border-[var(--border)]"
          )}
        >
          {children}
        </div>
      </section>
    )
  }

  return (
    <section
      data-slot="settings-section"
      className={cn("space-y-3", className)}
    >
      {content}
    </section>
  )
}

export { SettingsSection }
