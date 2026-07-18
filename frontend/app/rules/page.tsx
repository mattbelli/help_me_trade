import { redirect } from "next/navigation";

// Legacy sidebar UI retired (founder order 2026-07-18) — one UI world.
// Old deep-links land on the workspace.
export default function RulesRedirect() {
  redirect("/");
}
