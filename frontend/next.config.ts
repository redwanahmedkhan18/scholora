import type { NextConfig } from "next";
import { resolveApiBase } from "./config/api-base";

/**
 * The browser NEVER talks to the FastAPI port directly (it is not reachable from the
 * user's machine in a sandboxed preview). Every browser call uses a relative /api/... URL
 * and Next proxies it to the backend. Server components use INTERNAL_API_URL directly.
 *
 * Both paths resolve through `config/api-base`, which fails the build when a production
 * deployment has not set INTERNAL_API_URL (or set it to a loopback address) rather than
 * silently proxying to the build container's own port.
 */
const BACKEND = resolveApiBase();

const nextConfig: NextConfig = {
  reactStrictMode: true,
  allowedDevOrigins: ["*.e2b.app", "*.e2b.dev", "localhost", "127.0.0.1"],
  typescript: { ignoreBuildErrors: false },
  // Static generation forks a worker per CPU by default. On a small host that is what
  // gets the build OOM-killed once the page count grows, so cap it; the build is slower
  // but finishes in ~1 GB. Override with NEXT_BUILD_WORKERS on a bigger machine.
  experimental: {
    cpus: Number(process.env.NEXT_BUILD_WORKERS ?? 1),
    workerThreads: false,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-DNS-Prefetch-Control", value: "on" },
        ],
      },
    ];
  },
};

export default nextConfig;
