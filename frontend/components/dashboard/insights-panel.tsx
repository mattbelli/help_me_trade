export function InsightsPanel() {
  return (
    <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-background/60">
      <div className="border-b border-border px-4 py-3">
        <p className="text-sm font-medium">Session</p>
        <p className="text-xs text-muted-foreground">
          AI trade analysis — next bullet
        </p>
      </div>
      <div className="flex flex-1 items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Placeholder for LLM critiques, chat, and research summaries.
      </div>
    </div>
  );
}
