"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

export function ResearchMarkdown({
  markdown,
  className,
}: {
  markdown: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "research-md mx-auto max-w-[100ch] font-mono text-[length:var(--fs-title)] leading-relaxed text-[var(--fg-dim)]",
        "[&_h1]:mb-4 [&_h1]:font-heading [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:text-foreground",
        "[&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:font-heading [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-foreground",
        "[&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-foreground",
        "[&_p]:my-3 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-3 [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5",
        "[&_ol]:my-3 [&_ol]:list-decimal [&_ol]:space-y-1 [&_ol]:pl-5",
        "[&_li]:leading-relaxed",
        "[&_strong]:font-semibold [&_strong]:text-foreground",
        "[&_em]:italic",
        "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-[var(--border)] [&_blockquote]:pl-3 [&_blockquote]:text-[var(--fg-faint)]",
        "[&_hr]:my-6 [&_hr]:border-[var(--line-divider)]",
        "[&_a]:text-[var(--c-accent)] [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-foreground",
        "[&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[length:var(--fs-body)]",
        "[&_th]:border [&_th]:border-[var(--line-divider)] [&_th]:bg-[var(--bg-inset)] [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-medium [&_th]:text-foreground",
        "[&_td]:border [&_td]:border-[var(--line-divider)] [&_td]:px-2 [&_td]:py-1.5 [&_td]:align-top",
        className,
      )}
    >
      <div className="overflow-x-auto">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children, ...props }) => (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                {...props}
              >
                {children}
              </a>
            ),
          }}
        >
          {markdown}
        </ReactMarkdown>
      </div>
    </div>
  );
}
