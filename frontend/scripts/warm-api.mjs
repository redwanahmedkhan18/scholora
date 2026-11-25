/**
 * Wakes the API and waits until it can actually serve data, *before* `next build`
 * prerenders anything. Runs as npm's automatic `prebuild` step, which Vercel invokes as
 * part of `npm run build`.
 *
 * WHY THIS EXISTS
 * ---------------
 * The site uses Incremental Static Regeneration, so the first copy of every page is
 * rendered during the build and needs live data from the Render API. A free/standard
 * Render instance is stopped when idle and takes tens of seconds to accept connections
 * again. Without this step the build is a race: it usually loses, and it fails with
 * `API /countries -> 404` because Render's routing layer answers plain-text `Not Found`
 * while the instance is asleep - an error that says nothing about the real problem.
 *
 * This step makes the build deterministic without ever inventing data:
 *   - it polls the same endpoint the sitemap build depends on, so a 200 proves the
 *     database is reachable and serving rows;
 *   - a 503 keeps it waiting, because that is the API honestly reporting a dependency
 *     that is still starting (it also wakes a sleeping instance);
 *   - if the budget runs out it exits non-zero and prints the reason, so the Vercel log
 *     says "the API is unavailable" instead of a confusing 404 from an edge proxy.
 *
 * It never writes to the database and never fabricates content.
 */

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
// Same single implementation the app and next.config.ts use, so the origin cannot drift.
const { resolveApiBase } = require(path.resolve(here, "../config/api-base.js"));

const TIMEOUT_SECONDS = Number(process.env.API_WARMUP_TIMEOUT_SECONDS ?? 150);
const PROBE_PATH = "/api/v1/sitemap-data";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const base = resolveApiBase();
  const url = `${base}${PROBE_PATH}`;
  const deadline = Date.now() + TIMEOUT_SECONDS * 1000;
  let attempt = 0;
  let last = "no response";

  process.stdout.write(`[warm-api] waiting for ${base} to serve data (max ${TIMEOUT_SECONDS}s)\n`);

  while (Date.now() < deadline) {
    attempt += 1;
    try {
      const res = await fetch(url, {
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(45_000),
      });
      if (res.ok) {
        const body = await res.json();
        const counts = Object.entries(body)
          .map(([key, rows]) => `${key}=${Array.isArray(rows) ? rows.length : "?"}`)
          .join(" ");
        process.stdout.write(`[warm-api] API ready after ${attempt} attempt(s): ${counts}\n`);
        return;
      }
      // 503 is the API reporting a dependency that is not up yet; the request itself also
      // wakes a stopped instance. Keep waiting rather than failing on the first answer.
      last = `HTTP ${res.status} ${(await res.text()).slice(0, 200)}`;
    } catch (cause) {
      last = cause instanceof Error ? `${cause.name}: ${cause.message}` : String(cause);
    }
    process.stdout.write(`[warm-api] attempt ${attempt}: ${last}\n`);
    await sleep(5000);
  }

  process.stderr.write(
    `\n[warm-api] the API at ${base} did not become ready within ${TIMEOUT_SECONDS}s.\n` +
      `[warm-api] last result: ${last}\n\n` +
      "[warm-api] The build needs live data for prerendering, and failing here is better " +
      "than publishing empty pages.\n" +
      "[warm-api] Check, in this order:\n" +
      "  1. the Render service is deployed and not restarting;\n" +
      "  2. DATABASE_URL is the Supabase Session Pooler URL (IPv4), not db.<ref>.supabase.co;\n" +
      "  3. Render's health check path is /api/health/live, not /api/health/ready;\n" +
      "  4. GET /api/health/ready on the API returns \"ready\": true.\n" +
      "Raise API_WARMUP_TIMEOUT_SECONDS if the instance is merely cold.\n",
  );
  process.exit(1);
}

await main();
