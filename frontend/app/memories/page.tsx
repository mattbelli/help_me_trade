import { ComingSoonPage } from "@/components/placeholders/coming-soon-page";

export default function MemoriesPage() {
  return (
    <ComingSoonPage
      title="Memories"
      description="Stored agent memory and trading context."
      bullets={[
        "Browse what the agent remembers about your style and rules",
        "Edit or prune memories before they influence recommendations",
        "Link memories to specific symbols or journal entries",
      ]}
    />
  );
}
