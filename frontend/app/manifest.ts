import type { MetadataRoute } from "next";

// Installable-app manifest: lets Chromium browsers (Edge/Chrome) install the
// cockpit as a standalone window — own taskbar icon, no browser chrome.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "HMT — help_me_trade",
    short_name: "HMT",
    description: "Autonomous trading agent cockpit",
    start_url: "/",
    display: "standalone",
    background_color: "#131519",
    theme_color: "#131519",
    icons: [
      {
        src: "/icon.svg",
        sizes: "any",
        type: "image/svg+xml",
        purpose: "any",
      },
    ],
  };
}
