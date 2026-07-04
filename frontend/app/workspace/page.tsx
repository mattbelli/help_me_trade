import { redirect } from "next/navigation";

// The workspace owns the root URL now — keep old /workspace links working.
export default function WorkspaceRedirect() {
  redirect("/");
}
