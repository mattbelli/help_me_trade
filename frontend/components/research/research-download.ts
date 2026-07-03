import type { ResearchArtifact } from "@/lib/research";

function slugPart(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "report"
  );
}

export function researchMarkdownFilename(artifact: {
  symbol: string | null;
  topic: string;
  created_at: string;
}): string {
  const date = new Date(artifact.created_at);
  const ymd = [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("");
  const slug = slugPart(artifact.symbol?.trim() || artifact.topic.trim());
  return `research-${slug}-${ymd}.md`;
}

export function downloadResearchMarkdown(artifact: ResearchArtifact): void {
  const filename = researchMarkdownFilename(artifact);
  const blob = new Blob([artifact.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
