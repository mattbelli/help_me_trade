import WorkspacePage from "@/components/workspace/workspace-page";

// Page 1 of the widget-grid workspace IS the app's main URL (founder decision
// 2026-07-04). The old Overview page lost this slot; its features live in
// workspace widgets.
export default function Page() {
  return <WorkspacePage />;
}
