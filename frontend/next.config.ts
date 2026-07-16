import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  // Origins allowed to load Next.js dev resources (HMR etc). Next 16 blocks
  // cross-origin dev requests by default — if the access host isn't listed, the
  // dev client can't boot and the page never hydrates. Cover localhost, LAN, and
  // the Tailscale host/IP so the app works on this PC and over the tailnet.
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "192.168.8.157",
    "100.97.131.58",
    "mattpc",
    "mattpc.tail78aab8.ts.net",
  ],
  // Same-origin API proxy: the browser only ever talks to this server, which
  // forwards /api/* to the order-flow backend. Avoids cross-origin/extra-host
  // fragility when reaching it over Tailscale. Works in dev and `next start`.
  // Set ORDERFLOW_PROXY_TARGET to override (default = local minimal backend).
  async rewrites() {
    const backend = process.env.ORDERFLOW_PROXY_TARGET ?? "http://127.0.0.1:8001"
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }]
  },
}

export default nextConfig
