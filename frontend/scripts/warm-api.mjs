/**
 * Wait until the backend can serve live data, *before* `next build` prerenders anything.
 *
 * Runs as npm's automatic `prebuild`, which Vercel invokes as part of `npm run build`.
 *
 * WHY THIS EXISTS
 * ---------------
 * The site uses Incremental Static Regeneration, so the first copy of every page is rendered
 * during the build and needs live data from the Render API. A free/standard Render instance is
 * stopped when idle and takes tens of seconds to accept connections again, and a build that
 * happens to run first fails on what is purely a cold start.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 * --------------------------------
 * It never invents data and it never treats "no answer" as "no content". A build that cannot
 * reach the database must fail here, loudly, rather than publish pages that claim the site has
 * no universities.
 *
 * WHY IT NO LONGER RETRIES EVERYTHING
 * -----------------------------------
 * The previous version retried any non-2xx until the budget ran out and then reported "the
 * instance is merely cold". That message was wrong for the most common real cause - a wrong,
 * suspended or renamed `INTERNAL_API_URL`, where every path answers a plain-text 404 from
 * Render's edge and no amount of waiting can help. The classification now lives in
 * `scripts/api-readiness.mjs`, which distinguishes a transiently unavailable service from a
 * permanent configuration error and fails fast on the latter with the evidence that identified
 * it.
 *
 * CONFIGURATION (names only; values are never read from the repository)
 *   INTERNAL_API_URL            required for a production build; the Render web-service origin.
 *   INTERNAL_API_TOKEN          optional; sent as X-Internal-Token so first-party builds are
 *                               exempt from the public rate limit. Never logged.
 *   ALLOW_LOCAL_API             opt-out, for a deliberate production-mode build against a local API.
 *   API_WARMUP_TIMEOUT_SECONDS  total budget, default 240.
 *   SCHOLARA_REQUIRE_SITEMAP_DATA=1
 *                               fail the build when the API is healthy but the catalogue is
 *                               completely empty. Off by default so a legitimate empty
 *                               environment can still build.
 */

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describeCounts, failureLines, redactUrl, waitForApi } from "./api-readiness.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
// Same single implementation the app and next.config.ts use, so the origin cannot drift.
const { resolveApiBase } = require(path.resolve(here, "../config/api-base.js"));

const PROBE_PATH = "/api/v1/sitemap-data";
const DEFAULT_TIMEOUT_SECONDS = 240;
const PER_ATTEMPT_TIMEOUT_MS = 25_000;

function positiveInt(raw, fallback) {
  const value = Number.parseInt(String(raw ?? ""), 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** Never let a token reach the log, whichever way it arrived. */
function headers() {
  const token = (process.env.INTERNAL_API_TOKEN || "").trim();
  return token ? { "X-Internal-Token": token } : {};
}

async function main() {
  // resolveApiBase throws, with an actionable message, when a production build has no
  // INTERNAL_API_URL. That is the correct failure: a build container cannot render real pages
  // from an address it does not know.
  const base = resolveApiBase();
  const url = `${base}${PROBE_PATH}`;
  const budgetSeconds = positiveInt(process.env.API_WARMUP_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS);
  const deadlineMs = budgetSeconds * 1000;

  process.stdout.write(
    `[warm-api] waiting for ${redactUrl(base)} to serve data (max ${budgetSeconds}s)\n`,
  );

  const startedAt = Date.now();
  const result = await waitForApi({
    url,
    deadlineMs,
    timeoutMs: PER_ATTEMPT_TIMEOUT_MS,
    headers: headers(),
    log: (line) => process.stdout.write(`[warm-api] ${line}\n`),
  });

  if (!result.ok) {
    for (const line of failureLines({
      base,
      last: result.last,
      deadlineMs,
      attempts: result.attempts,
      elapsedMs: Date.now() - startedAt,
    })) {
      process.stderr.write(`${line}\n`);
    }
    process.exit(1);
  }

  const counts = result.counts ?? {};
  const total = result.total ?? 0;
  if (total === 0) {
    const detail =
      "the API is reachable but returned no catalogue rows at all " +
      `(${describeCounts(counts)}). Prerendering now would publish a site that claims to have ` +
      "no universities, programmes or scholarships.";
    if (/^(1|true|yes)$/i.test(String(process.env.SCHOLARA_REQUIRE_SITEMAP_DATA || "").trim())) {
      process.stderr.write(`[warm-api] ${detail}\n`);
      process.stderr.write(
        "[warm-api] SCHOLARA_REQUIRE_SITEMAP_DATA is set, so this is a failure. Check DATABASE_URL " +
          "on Render and whether the database has been seeded.\n",
      );
      process.exit(1);
    }
    process.stdout.write(`[warm-api] WARNING: ${detail}\n`);
    process.stdout.write(
      "[warm-api] Set SCHOLARA_REQUIRE_SITEMAP_DATA=1 on the Vercel project to make this fatal.\n",
    );
  }
}

// resolveApiBase throws, with its own actionable message, when a production build has no usable
// origin. Print that message alone: a raw unhandled-rejection stack buries the one line an
// operator needs, and this step's whole job is to fail readably.
try {
  await main();
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.stderr.write(
    "[warm-api] The build cannot prerender pages without a reachable backend, so it stops here.\n",
  );
  process.exit(1);
}
