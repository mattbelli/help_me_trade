import { redirect } from "next/navigation";

// The legacy /charts multi-chart grid is retired — the workspace's chart
// widgets supersede it. Kept as a redirect so old bookmarks / links land.
export default function ChartsRoute() {
  redirect("/workspace");
}
