import type { NextConfig } from "next";

// Allow extra dev origins via ALLOWED_DEV_ORIGINS env var (comma-separated).
const extraOrigins = (process.env.ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  // In dev, Next may proxy requests based on the request origin/host.
  // Allow common local origins so `next dev --hostname 0.0.0.0` works
  // when users access via any LAN IP, localhost, or 127.0.0.1.
  allowedDevOrigins: ["localhost", "127.0.0.1", ...extraOrigins],
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "img.clerk.com",
      },
    ],
  },
  typescript: {
    // The production host (4 GB) runs out of memory in `next build`'s whole-program type check.
    // CI type-checks (`make frontend-typecheck`) and builds every commit before the deploy runs,
    // so the deploy workflow sets MC_SKIP_BUILD_TYPECHECK=1; local and CI builds still check.
    ignoreBuildErrors: process.env.MC_SKIP_BUILD_TYPECHECK === "1",
  },
};

export default nextConfig;
