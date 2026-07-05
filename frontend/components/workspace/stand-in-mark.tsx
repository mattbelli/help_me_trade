import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";

/**
 * The `stand_in` brand mark (see brand/README.md — canonical geometry,
 * viewBox 0 0 120 120). Hollow bar = the user, stepped away; solid bar one
 * step up = the agent standing in.
 *
 * - default: single-color cut — both paths `currentColor` (tint via CSS
 *   `color`, e.g. the channel accent). Hierarchy comes from hollow vs solid.
 * - `duo`: themed two-grey cut — hollow `--fg-dim`, solid `--foreground`
 *   (maps the brand greys onto theme neutrals per the brand spec).
 */
export function StandInMark({
  duo = false,
  className,
  style,
}: {
  duo?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 120 120"
      className={cn("shrink-0", className)}
      style={style}
      aria-hidden
    >
      <path
        d="M34 38 h20 v54 h-20 Z M39.5 43.5 h9 v43 h-9 Z"
        fill={duo ? "var(--fg-dim)" : "currentColor"}
        fillRule="evenodd"
      />
      <rect
        x="66"
        y="26"
        width="20"
        height="54"
        fill={duo ? "var(--foreground)" : "currentColor"}
      />
    </svg>
  );
}
