import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  // Allow opening the dev app via your LAN IP (e.g. phone/tablet on same Wi‑Fi).
  // Same machine: http://localhost:3001 avoids this entirely.
  allowedDevOrigins: ["192.168.8.157"],
}

export default nextConfig
