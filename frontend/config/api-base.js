/**
 * Resolves the backend origin and the public site origin for Scholara's frontend.
 *
 * WHY THIS IS PLAIN COMMONJS AND NOT TYPESCRIPT
 * ---------------------------------------------
 * `next.config.ts` needs the backend origin to build the `/api/:path*` rewrite, and the
 * server-side data client needs the identical value. Sharing the code is the only way to
 * guarantee they cannot disagree - but Next transpiles the config file to
 * `next.config.compiled.js` and `require()`s the result at build time. Node resolves
 * `require("./src/lib/api-base")` to `.js`/`.json`/`.node` only; an extensionless `.ts`
 * target is invisible to it. That is exactly how the Vercel build failed with
 * `Cannot find module './src/lib/api-base'` while the same build passed locally on a Node
 * version that strips TypeScript types natively.
 *
 * A `.js` file in a package without `"type": "module"` is CommonJS, so both the compiled
 * config and the Next bundle can load it on any Node version. JSDoc types keep the
 * TypeScript side fully checked (`allowJs` is on in tsconfig.json).
 *
 * BEHAVIOUR
 * ---------
 * production (`NODE_ENV=production`, i.e. every `next build` and every Vercel deploy)
 *   - INTERNAL_API_URL is required, must be an absolute http(s) URL, and must use https
 *     unless it points at loopback.
 *   - Loopback is refused, because that is the exact mistake this module exists to catch.
 *     A developer who really does want a production-mode build against a local API sets
 *     ALLOW_LOCAL_API=true deliberately.
 *
 * development (`next dev`)
 *   - INTERNAL_API_URL is optional and defaults to the local API.
 *
 * Previously both call sites did `process.env.INTERNAL_API_URL ?? "http://127.0.0.1:8000"`.
 * In a Vercel build with the variable missing that did not stop; it fetched the build
 * container's own port, got ECONNREFUSED, and pages that treated a failure as "no data"
 * published themselves empty. The build is the correct place to fail - and only for a
 * deployment that genuinely did not supply the variable.
 */

"use strict";

/** Loopback targets a developer may point a local build at. */
var DEV_API_BASE = "http://127.0.0.1:8000";
var DEV_SITE_URL = "http://localhost:3000";

/** @type {Set<string>} */
var LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"]);

/**
 * Read an environment variable as a trimmed string.
 * Next inlines only NEXT_PUBLIC_* into the client bundle, so a server-only variable reads
 * as undefined in the browser. `api.js` therefore refuses to run there at all.
 * @param {string} name
 * @returns {string}
 */
function env(name) {
  var value = process.env[name];
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Dashboard copy/paste routinely produces surrounding quotes; they are never part of a URL.
 * @param {string} value
 * @returns {string}
 */
function unquote(value) {
  return value.replace(/^["']+|["']+$/g, "").trim();
}

/**
 * True when this is a production runtime (build, Vercel deploy or self-hosted prod).
 * @returns {boolean}
 */
function isProductionBuild() {
  if (process.env.NODE_ENV === "production") return true;
  return env("VERCEL_ENV") === "production";
}

/**
 * Explicit opt-out for a production-mode build against a local API. Off by default.
 * @returns {boolean}
 */
function localApiAllowed() {
  return /^(1|true|yes)$/i.test(unquote(env("ALLOW_LOCAL_API")));
}

/**
 * Only meaningful on the server; a browser bundle must never build absolute API URLs.
 * @returns {boolean}
 */
function isServer() {
  return typeof window === "undefined";
}

/**
 * @param {string} hostname
 * @returns {boolean}
 */
function isLoopback(hostname) {
  return LOOPBACK_HOSTS.has(hostname.toLowerCase());
}

/**
 * Validate an absolute base URL and return it without a trailing slash.
 * Throws a message naming the variable and the fix, never the value's contents.
 * @param {string} value
 * @param {string} source
 * @returns {string}
 */
function validateOrigin(value, source) {
  /** @type {URL} */
  var parsed;
  try {
    parsed = new URL(value);
  } catch (cause) {
    throw new Error(
      "[scholara] " +
        source +
        " is not a valid absolute URL. Expected something like https://your-api.example.com" +
        " - got " +
        JSON.stringify(value) +
        ".",
    );
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(
      "[scholara] " + source + " must use http:// or https:// (got " + parsed.protocol + ").",
    );
  }
  if (parsed.search || parsed.hash) {
    throw new Error("[scholara] " + source + " must not contain a query string or fragment.");
  }
  return (parsed.origin + parsed.pathname).replace(/\/+$/, "");
}

/**
 * The backend origin, e.g. `https://scholara-cfch.onrender.com`.
 * Returns "" in the browser so a stray client import fails with a clear message instead of
 * silently issuing requests to an address it cannot know.
 * @returns {string}
 */
function resolveApiBase() {
  if (!isServer()) return "";

  var raw = unquote(env("INTERNAL_API_URL"));
  var production = isProductionBuild();
  var allowLocal = localApiAllowed();

  if (!raw) {
    if (production) {
      throw new Error(
        "[scholara] INTERNAL_API_URL is not set. Server-side rendering cannot reach the " +
          "FastAPI backend, so this build would publish empty pages. Set it in the Vercel " +
          "project (Settings -> Environment Variables) to the Render API origin, e.g. " +
          "INTERNAL_API_URL=https://scholara-cfch.onrender.com. For a local production-mode " +
          "build against a local API, set ALLOW_LOCAL_API=true as well.",
      );
    }
    return DEV_API_BASE;
  }

  var base = validateOrigin(raw, "INTERNAL_API_URL");
  var parsed = new URL(base);
  var host = parsed.hostname;

  if (production) {
    if (isLoopback(host) && !allowLocal) {
      throw new Error(
        "[scholara] INTERNAL_API_URL points at loopback (" +
          host +
          ") during a production build. Inside a Vercel build or a Render container that " +
          "address is the container itself, not the backend, so every request would fail. " +
          "Set INTERNAL_API_URL to the deployed HTTPS API origin. If this is a deliberate " +
          "local production-mode build, set ALLOW_LOCAL_API=true.",
      );
    }
    if (parsed.protocol !== "https:" && !isLoopback(host)) {
      throw new Error(
        "[scholara] INTERNAL_API_URL must use https:// in production; plain http would " +
          "send the internal API token in clear text (host: " +
          host +
          ").",
      );
    }
  }

  return base;
}

/**
 * The public site origin used for canonical URLs, Open Graph tags, robots and the sitemap.
 *
 * It must never be a localhost address in production, but unlike the API origin it does not
 * have to be supplied by hand on Vercel: Vercel injects the deployment's own domain
 * (`VERCEL_PROJECT_PRODUCTION_URL` / `VERCEL_URL`), which is exactly the right answer when a
 * custom domain has not been attached yet.
 * @returns {string}
 */
function resolveSiteUrl() {
  var explicit = unquote(env("NEXT_PUBLIC_SITE_URL"));
  if (explicit) return validateOrigin(explicit, "NEXT_PUBLIC_SITE_URL");

  var vercelHost =
    unquote(env("VERCEL_PROJECT_PRODUCTION_URL")) || unquote(env("VERCEL_URL"));
  if (vercelHost) {
    return validateOrigin("https://" + vercelHost.replace(/^https?:\/\//, ""), "VERCEL_URL");
  }

  if (isProductionBuild() && !localApiAllowed()) {
    throw new Error(
      "[scholara] NEXT_PUBLIC_SITE_URL is not set and no Vercel deployment URL was found. " +
        "Canonical URLs, Open Graph tags and the sitemap would all be published as " +
        "localhost. Set NEXT_PUBLIC_SITE_URL to the production site origin (not a localhost " +
        "address).",
    );
  }
  return DEV_SITE_URL;
}

module.exports = {
  resolveApiBase: resolveApiBase,
  resolveSiteUrl: resolveSiteUrl,
  isServer: isServer,
  isProductionBuild: isProductionBuild,
};
