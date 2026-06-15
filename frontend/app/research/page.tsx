import { ComingSoonPage } from "@/components/placeholders/coming-soon-page";

export default function ResearchPage() {
  return (
    <ComingSoonPage
      title="Research"
      description="Deep stock research and external report ingestion."
      bullets={[
        "Pull structured research from your preferred providers",
        "Save summaries alongside positions and trades",
        "Feed context into the AI insights panel",
      ]}
    />
  );
}
