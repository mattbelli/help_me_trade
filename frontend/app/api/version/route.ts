import { promises as fs } from "fs";
import path from "path";

// Next writes .next/BUILD_ID at build time; it's stable for the life of a
// running server, so read it once and cache in module scope. force-dynamic
// keeps this out of the static shell so a stale deploy can't bake in an old id.
export const dynamic = "force-dynamic";

let cachedBuildId: string | null = null;

async function readBuildId(): Promise<string | null> {
  if (cachedBuildId) return cachedBuildId;
  try {
    const raw = await fs.readFile(
      path.join(process.cwd(), ".next", "BUILD_ID"),
      "utf8",
    );
    cachedBuildId = raw.trim() || null;
    return cachedBuildId;
  } catch {
    // Dev (no build) or unreadable → report null; the client fails soft.
    return null;
  }
}

export async function GET() {
  const buildId = await readBuildId();
  return Response.json({ buildId });
}
