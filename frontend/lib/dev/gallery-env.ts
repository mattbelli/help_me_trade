"use client";

/** Dev-only environment shims for /dev/gallery.
 *
 * Widgets read their data through `apiFetch` → global `fetch`, and their live
 * prices through a WebSocket to the backend. The gallery replaces BOTH at the
 * boundary — from the gallery side only, so not a line of widget code changes:
 *
 *   fetch      → frozen fixture JSON, chosen by `?state=`
 *   WebSocket  → a socket to the backend that never opens (no reconnect storm,
 *                no live ticks, so two screenshots agree)
 *
 * The interception itself is installed pre-hydration by the inline guard in
 * `app/dev/gallery/fetch-guard.ts`; this module registers the fixture HANDLER
 * the guard defers to, and releases anything the guard parked while waiting.
 * Installed at MODULE scope, not in an effect — widget effects fire on mount.
 */
import {
  isGalleryState,
  matchFixture,
  type GalleryState,
} from "./fixtures/routes";

export const UNMATCHED_EVENT = "hmt-gallery-unmatched";

const unmatched = new Set<string>();

export function unmatchedPaths(): string[] {
  return [...unmatched].sort();
}

export function readGalleryState(search: string): GalleryState {
  const value = new URLSearchParams(search).get("state");
  return isGalleryState(value) ? value : "default";
}

type GalleryWindow = Window & {
  __hmtGalleryGuard?: boolean;
  __hmtGalleryFixture?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  __hmtGalleryFlush?: () => void;
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): URL | null {
  try {
    const raw =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    return new URL(raw, window.location.origin);
  } catch {
    return null;
  }
}

function makeHandler(state: GalleryState) {
  return (input: RequestInfo | URL): Promise<Response> => {
    const url = requestUrl(input);
    if (!url) return Promise.resolve(json({}));

    // A promise that never settles is what "loading" actually looks like to a
    // widget: no data, no error, spinner held.
    if (state === "loading") return new Promise<Response>(() => {});
    if (state === "error") {
      return Promise.resolve(json({ error: "gallery: induced failure" }, 503));
    }

    const body = matchFixture(url, state);
    if (body === undefined) {
      if (!unmatched.has(url.pathname)) {
        unmatched.add(url.pathname);
        console.warn(`[gallery] no fixture for ${url.pathname}`);
        window.dispatchEvent(new CustomEvent(UNMATCHED_EVENT));
      }
      return Promise.resolve(json({}));
    }
    return Promise.resolve(json(body));
  };
}

/** The backend surface: every /api/* route plus the bare /health probe. */
function isBackendPath(pathname: string): boolean {
  return pathname.startsWith("/api/") || pathname === "/health";
}

/** Fallback path: the guard normally owns the wrapper, but if this module is
 *  ever reached without it (a client-side nav into the route), wrap here. */
function installFallbackWrapper(handler: (input: RequestInfo | URL) => Promise<Response>) {
  const realFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (!url || !isBackendPath(url.pathname)) return realFetch(input, init);
    return handler(input);
  };
}

let installed = false;

export function installGalleryEnv(): GalleryState {
  if (typeof window === "undefined") return "default";
  const state = readGalleryState(window.location.search);
  if (installed || process.env.NODE_ENV === "production") return state;
  installed = true;

  const win = window as GalleryWindow;
  const handler = makeHandler(state);
  win.__hmtGalleryFixture = handler;

  if (win.__hmtGalleryGuard) {
    win.__hmtGalleryFlush?.();
  } else {
    installFallbackWrapper(handler);
  }
  return state;
}

// Self-installing: importing this module from the gallery route is enough, and
// module evaluation happens before React mounts anything in the route subtree.
installGalleryEnv();
