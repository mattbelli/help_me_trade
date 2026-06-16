"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";

import { ToolOutput } from "@/components/ai-elements/tool";

type ResearchOutput = {
  artifact_id?: number;
  topic?: string;
  symbol?: string | null;
  depth?: string;
  status?: string;
  markdown?: string;
  error?: string;
};

function isResearchOutput(value: unknown): value is ResearchOutput {
  return (
    typeof value === "object" &&
    value !== null &&
    ("artifact_id" in value || "topic" in value)
  );
}

export function ResearchToolOutput({
  output,
  errorText,
}: {
  output: unknown;
  errorText?: string;
}) {
  if (errorText) {
    return <ToolOutput output={output} errorText={errorText} />;
  }

  if (!isResearchOutput(output) || !output.artifact_id) {
    return <ToolOutput output={output} errorText={errorText} />;
  }

  const summary =
    output.markdown?.split("\n").find((line) => line.trim())?.replace(/^#+\s*/, "") ||
    output.topic ||
    "Research report";

  return (
    <div className="space-y-3 rounded-md border border-border/70 bg-muted/30 p-3 text-sm">
      <div>
        <p className="font-medium">{output.topic ?? "Research complete"}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {output.depth ? `${output.depth} depth` : "research"}
          {output.symbol ? ` · ${output.symbol}` : ""}
          {output.status ? ` · ${output.status}` : ""}
        </p>
      </div>
      {summary ? (
        <p className="line-clamp-3 text-muted-foreground leading-relaxed">
          {summary}
        </p>
      ) : null}
      <Link
        href={`/research?id=${output.artifact_id}`}
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
      >
        <ExternalLink className="size-3.5" />
        View full report
      </Link>
    </div>
  );
}
