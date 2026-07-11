import { redirect } from "next/navigation";

// The legacy /trading page (single-symbol chart + watchlist) is retired — the
// workspace supersedes it. Kept as a redirect so old bookmarks / deep-links land.
export default function TradingRoute() {
  redirect("/workspace");
}
