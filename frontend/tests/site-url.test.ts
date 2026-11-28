/**
 * The canonical site-origin contract.
 *
 * `NEXT_SITE_URL` is the authoritative variable. Before this contract existed the project read
 * `NEXT_PUBLIC_SITE_URL`, which a production deployment did not set — so `resolveSiteUrl()` fell
 * through to the development default and the live site published `http://localhost:3000` in its
 * sitemap, its canonical tags, its Open Graph URLs and its JSON-LD. 209 sitemap entries, 6
 * localhost strings on a programme page and 14 on the homepage.
 *
 * Two things therefore have to be true and are tested here:
 *
 *   1. resolution is correct — precedence, validation, slash normalisation, and a hard failure
 *      rather than a silent localhost in production;
 *   2. there is exactly ONE resolution path — sitemap, canonical, Open Graph and JSON-LD all read
 *      the single `SITE_URL`, so a future change cannot fix one surface and miss the others.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..");
const src = join(frontend, "src");

const require = createRequire(import.meta.url);
const { resolveSiteUrl, resolvePublishedOrigin } = require(join(frontend, "config", "api-base.js"));

const PRODUCTION_ORIGIN = "https://scholora-6ela.vercel.app";

/** Environment keys that influence resolution; every test starts from a clean slate. */
const RELEVANT = /^(NEXT_SITE_URL|NEXT_PUBLIC_SITE_URL|VERCEL_PROJECT_PRODUCTION_URL|VERCEL_URL|VERCEL_ENV|NODE_ENV|ALLOW_LOCAL_API)$/;

/** Run `fn` with exactly the supplied environment, then restore it. */
function withEnv(env: Record<string, string>, fn: () => void) {
  const saved: Record<string, string | undefined> = {};
  for (const key of Object.keys(process.env)) {
    if (RELEVANT.test(key)) {
      saved[key] = process.env[key];
      delete process.env[key];
    }
  }
  Object.assign(process.env, env);
  try {
    fn();
  } finally {
    for (const key of Object.keys(process.env)) {
      if (RELEVANT.test(key)) delete process.env[key];
    }
    for (const [key, value] of Object.entries(saved)) {
      if (value !== undefined) process.env[key] = value;
    }
  }
}

/** Every file under src/, for the single-resolution-path assertions. */
function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(full)) out.push(full);
  }
  return out;
}

// ---------------------------------------------------------------- 1-6: resolution

test("1. NEXT_SITE_URL supplies the production canonical origin", () => {
  withEnv({ NODE_ENV: "production", NEXT_SITE_URL: PRODUCTION_ORIGIN }, () => {
    assert.equal(resolveSiteUrl(), PRODUCTION_ORIGIN);
  });
});

test("2. a production build without a site origin fails closed, never localhost", () => {
  withEnv({ NODE_ENV: "production" }, () => {
    assert.throws(
      () => resolveSiteUrl(),
      (error: unknown) => {
        const message = String((error as Error).message);
        assert.match(message, /NEXT_SITE_URL is not set/);
        assert.doesNotMatch(message, /localhost:3000$/);
        return true;
      },
    );
  });
});

test("3. an invalid or loopback-shaped value is rejected in production", () => {
  for (const bad of ["not-a-url", "ftp://example.com", "https://example.com/?q=1", "https://example.com/#f"]) {
    withEnv({ NODE_ENV: "production", NEXT_SITE_URL: bad }, () => {
      assert.throws(() => resolveSiteUrl(), `expected rejection of ${bad}`);
    });
  }
});

test("4. trailing slashes are normalised away", () => {
  withEnv({ NODE_ENV: "production", NEXT_SITE_URL: `${PRODUCTION_ORIGIN}///` }, () => {
    assert.equal(resolveSiteUrl(), PRODUCTION_ORIGIN);
  });
  withEnv({ NODE_ENV: "production", NEXT_SITE_URL: `"${PRODUCTION_ORIGIN}/"` }, () => {
    assert.equal(resolveSiteUrl(), PRODUCTION_ORIGIN, "surrounding quotes are stripped too");
  });
});

test("5. the localhost fallback exists only outside a production build", () => {
  withEnv({ NODE_ENV: "development" }, () => {
    assert.equal(resolveSiteUrl(), "http://localhost:3000");
  });
  // A production build may opt out explicitly, which is how a local production-mode build works.
  withEnv({ NODE_ENV: "production", ALLOW_LOCAL_API: "true" }, () => {
    assert.equal(resolveSiteUrl(), "http://localhost:3000");
  });
  // ...but a plain production build may not.
  withEnv({ NODE_ENV: "production" }, () => {
    assert.throws(() => resolveSiteUrl());
  });
});

test("6. NEXT_SITE_URL takes precedence over the deprecated NEXT_PUBLIC_SITE_URL", () => {
  withEnv(
    {
      NODE_ENV: "production",
      NEXT_SITE_URL: PRODUCTION_ORIGIN,
      NEXT_PUBLIC_SITE_URL: "https://legacy.example.com",
    },
    () => {
      assert.equal(resolveSiteUrl(), PRODUCTION_ORIGIN);
    },
  );
});

test("6b. the deprecated alias still works on its own, so existing deployments do not break", () => {
  withEnv({ NODE_ENV: "production", NEXT_PUBLIC_SITE_URL: "https://legacy.example.com" }, () => {
    assert.equal(resolveSiteUrl(), "https://legacy.example.com");
  });
});

// ---------------------------------------------------------------- 7-12: single resolution path

const RELATIVE_SRC = (file: string) => relative(frontend, file).split("\\").join("/");

test("7. the sitemap derives its origin from the shared resolver", () => {
  const sitemap = readFileSync(join(src, "app", "sitemap.ts"), "utf8");
  assert.match(sitemap, /import \{[^}]*publishedOrigin[^}]*\} from "@\/lib\/api"/);
  assert.match(sitemap, /publishedOrigin\(settings\)/, "the base must come from publishedOrigin");
  // And it must not invent an origin of its own.
  assert.doesNotMatch(sitemap, /["'`]https?:\/\//, "no hardcoded absolute origin in the sitemap");
});

test("8-10. canonical, Open Graph and JSON-LD all read the same resolver", () => {
  const layout = readFileSync(join(src, "app", "layout.tsx"), "utf8");
  assert.match(layout, /import \{[^}]*publishedOrigin[^}]*\} from "@\/lib\/api"/);
  // metadataBase (which feeds every relative canonical and OG image) …
  assert.match(layout, /metadataBase: new URL\(publishedOrigin\(s\)\)/);
  // … the Open Graph url …
  assert.match(layout, /url:\s*publishedOrigin\(s\)/);
  // … and the JSON-LD url, which is the third consumer of the same value.
  assert.match(layout, /url:\s*publishedOrigin\(settings\)/);
});

test("7-11. robots derives from the same origin", () => {
  const robots = readFileSync(join(src, "app", "robots.ts"), "utf8");
  assert.match(robots, /import \{[^}]*publishedOrigin[^}]*\} from "@\/lib\/api"/);
  assert.match(robots, /publishedOrigin\(settings\)/);
});

test("there is exactly one site-origin resolution implementation", () => {
  const files = [...walk(src), join(frontend, "config", "api-base.js")];
  const definers = files.filter((file) =>
    /function\s+resolveSiteUrl\s*\(/.test(readFileSync(file, "utf8")),
  );
  assert.deepEqual(
    definers.map(RELATIVE_SRC),
    ["config/api-base.js"],
    "a second implementation would let one surface drift from the others",
  );

  // SITE_URL is a single module-level constant, so nothing can compute a competing origin.
  const api = readFileSync(join(src, "lib", "api.ts"), "utf8");
  assert.match(api, /export const SITE_URL = resolveSiteUrl\(\);/);
});

test("no client component pulls the server-only site origin into a browser bundle", () => {
  const offenders = walk(src)
    .filter((file) => /^\s*["']use client["']/m.test(readFileSync(file, "utf8")))
    .filter((file) => /from\s+["']@\/lib\/api["']/.test(readFileSync(file, "utf8")));
  assert.deepEqual(
    offenders.map(RELATIVE_SRC),
    [],
    "NEXT_SITE_URL has no NEXT_PUBLIC_ prefix, so it is server-only by design",
  );
});

test("a database seo.site_url of localhost cannot override the env origin in production", () => {
  // The real production defect: the database carried seo.site_url = http://localhost:3000 and
  // every consumer preferred it, so setting NEXT_SITE_URL alone changed nothing.
  withEnv({ NODE_ENV: "production", NEXT_SITE_URL: PRODUCTION_ORIGIN }, () => {
    assert.equal(
      resolvePublishedOrigin({ site_url: "http://localhost:3000" }),
      PRODUCTION_ORIGIN,
      "in production the environment must win over an admin-editable database value",
    );
    assert.equal(resolvePublishedOrigin({}), PRODUCTION_ORIGIN);
  });
  // In development the setting is still honoured, because localhost is the right answer there.
  withEnv({ NODE_ENV: "development" }, () => {
    assert.equal(
      resolvePublishedOrigin({ site_url: "http://localhost:3000" }),
      "http://localhost:3000",
    );
  });
});

test("a production build with no origin throws rather than publishing the database value", () => {
  withEnv({ NODE_ENV: "production" }, () => {
    assert.throws(() => resolvePublishedOrigin({ site_url: "http://localhost:3000" }));
  });
});

test("the serialised settings payload cannot echo a stale database origin", () => {
  // `flattenSettings` spreads the raw settings, which carries the database's nested `seo` object
  // onto every page. Its copy of the origin is normalised too, so a stale
  // `seo.site_url = http://localhost:3000` cannot reach production HTML - or round-trip back into
  // the database through the admin form.
  const api = readFileSync(join(src, "lib", "api.ts"), "utf8");
  assert.match(
    api,
    /seo:\s*\{\s*\.\.\.seo,\s*site_url:\s*publishedOrigin\(/,
    "the nested seo.site_url must be normalised, not passed through",
  );
  assert.match(api, /site_url:\s*publishedOrigin\(\{ site_url: seo\.site_url \}\)/);
});

test("12. no production resolution path can yield a localhost origin", () => {
  // Every production-shaped environment must resolve to a real origin or throw. This is what
  // keeps `localhost:3000` out of the sitemap, canonical tags, Open Graph and JSON-LD.
  const environments: Array<Record<string, string>> = [
    { NODE_ENV: "production", NEXT_SITE_URL: PRODUCTION_ORIGIN },
    { NODE_ENV: "production", VERCEL_ENV: "production", NEXT_SITE_URL: PRODUCTION_ORIGIN },
    { NODE_ENV: "production", VERCEL_PROJECT_PRODUCTION_URL: "scholora-6ela.vercel.app" },
    { NODE_ENV: "production", VERCEL_URL: "scholora-6ela-abc123.vercel.app" },
  ];
  for (const environment of environments) {
    withEnv(environment, () => {
      const resolved = resolveSiteUrl();
      assert.doesNotMatch(resolved, /localhost|127\.0\.0\.1/, JSON.stringify(environment));
      assert.match(resolved, /^https:\/\//);
    });
  }
  // With no origin at all, production throws rather than emitting localhost.
  withEnv({ NODE_ENV: "production", VERCEL_ENV: "production" }, () => {
    assert.throws(() => resolveSiteUrl());
  });
});
