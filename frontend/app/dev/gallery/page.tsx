import { notFound } from "next/navigation";

import { GALLERY_FETCH_GUARD } from "./fetch-guard";
import { GalleryClient } from "./gallery-client";

// Dev-only render harness. Never reachable in a production build.
export const dynamic = "force-dynamic";

export const metadata = {
  title: "widget gallery",
};

export default async function GalleryPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (process.env.NODE_ENV === "production") notFound();

  const params = await searchParams;
  const one = (key: string): string | null => {
    const value = params[key];
    return Array.isArray(value) ? (value[0] ?? null) : (value ?? null);
  };

  return (
    <>
      {/* Runs during HTML parse — before the LAYOUT hydrates and its
          ThemeProvider fires the first real API call. */}
      <script
        id="hmt-gallery-guard"
        dangerouslySetInnerHTML={{ __html: GALLERY_FETCH_GUARD }}
      />
      <GalleryClient widgetId={one("widget")} stateParam={one("state")} />
    </>
  );
}
