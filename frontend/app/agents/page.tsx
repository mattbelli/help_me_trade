import { ComingSoonPage } from "@/components/placeholders/coming-soon-page";

export default function AgentsPage() {
  return (
    <ComingSoonPage
      title="Agents"
      description="Run trading agents and optional CLI workflows."
      bullets={[
        "Launch analysis agents against your live snapshot",
        "View run history, logs, and outputs",
        "Optional terminal-style interface for power users",
      ]}
    />
  );
}
