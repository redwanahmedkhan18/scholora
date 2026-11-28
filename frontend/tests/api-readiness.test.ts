/**
 * The prebuild readiness probe is the only thing between a sleeping backend and a failed
 * Vercel deploy, and the only thing between a *wrong* backend origin and a build that
 * publishes empty pages. Both failures looked identical in the old implementation: it retried
 * everything for the whole budget and then said "the instance is merely cold".
 *
 * These tests drive the classifier and the retry loop with an injected clock and fetch, so the
 * cold-start, gateway, misconfiguration and malformed-response cases are each pinned exactly,
 * with no network and no real waiting.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  AMBIGUOUS_BUDGET_MS,
  AMBIGUOUS_MAX_ATTEMPTS,
  classify,
  describeCounts,
  failureLines,
  probeOnce,
  redactUrl,
  waitForApi,
} from "../scripts/api-readiness.mjs";

const JSON_OK = {
  countries: [{ slug: "a", updated: "2026-01-01T00:00:00" }],
  universities: [{ slug: "u", country: "a", updated: "2026-01-01T00:00:00" }],
  programs: [{ slug: "p", updated: "2026-01-01T00:00:00" }],
  scholarships: [],
  faculty: [],
  faculties: [],
  services: [],
  success_stories: [],
  events: [],
  posts: [],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function textResponse(body: string, status = 404) {
  return new Response(body, { status, headers: { "content-type": "text/html" } });
}

/**
 * The exact signature observed on https://scholara-cfch.onrender.com during Campaign 11A:
 * 404, text/plain, 10 bytes, `x-render-routing: no-server`, Cloudflare edge. A hostname that
 * certainly has no Render service (a random subdomain) answers byte-for-byte the same.
 */
function renderNoServerResponse() {
  return new Response("Not Found\n", {
    status: 404,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "content-length": "10",
      "x-render-routing": "no-server",
      server: "cloudflare",
    },
  });
}

/** A 404 from a host where a Render service *does* exist (its service headers are present). */
function renderServiceMissingRouteResponse() {
  return new Response("Not Found", {
    status: 404,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "rndr-id": "6b2f6639-2aed-44db",
      "x-render-origin-server": "gunicorn",
    },
  });
}

/** A fetch whose calls are scripted, and a clock that only moves when the code sleeps. */
function harness(script: Array<() => Response | Promise<Response>>) {
  let clock = 1_000_000;
  let index = 0;
  const delays: number[] = [];
  const seen: string[] = [];
  return {
    delays,
    seen,
    fetchImpl: (async (url: string) => {
      seen.push(String(url));
      const step = script[Math.min(index, script.length - 1)];
      index += 1;
      return step();
    }) as unknown as typeof fetch,
    sleepImpl: async (ms: number) => {
      delays.push(ms);
      clock += ms;
    },
    nowImpl: () => clock,
  };
}

// ---------------------------------------------------------------- classification

test("a healthy sitemap response is ready and reports per-list counts", () => {
  const verdict = classify({
    status: 200,
    contentType: "application/json",
    bodyText: JSON.stringify(JSON_OK),
  });
  assert.equal(verdict.kind, "ready");
  assert.equal(verdict.counts?.countries, 1);
  assert.equal(verdict.total, 3);
});

test("an empty but well-formed catalogue is ready, not an error", () => {
  const empty = Object.fromEntries(
    Object.keys(JSON_OK).map((key) => [key, [] as unknown[]]),
  );
  const verdict = classify({
    status: 200,
    contentType: "application/json",
    bodyText: JSON.stringify(empty),
  });
  assert.equal(verdict.kind, "ready");
  assert.equal(verdict.total, 0);
});

test("gateway statuses are transient: the instance may still be booting", () => {
  for (const status of [429, 502, 503, 504]) {
    const verdict = classify({ status, contentType: "text/plain", bodyText: "not ready" });
    assert.equal(verdict.kind, "transient", `status ${status}`);
  }
});

test("a plain-text 404 with no Render marker is AMBIGUOUS, not retryable-as-asleep", () => {
  // Campaign 11A: this used to be `transient`/`edge-404` and burnt the full cold-start budget.
  // Without evidence that a service exists, the honest answer is "we cannot tell".
  const verdict = classify({ status: 404, contentType: "text/html", bodyText: "Not Found" });
  assert.equal(verdict.kind, "ambiguous");
  assert.equal(verdict.code, "edge-404-unconfirmed");
});

test("Render's x-render-routing: no-server is conclusive and permanent", () => {
  // Render's own routing layer states that nothing is attached to the hostname. A sleeping
  // instance still has a server, so this must never be reported as a cold start.
  const verdict = classify({
    status: 404,
    contentType: "text/plain; charset=utf-8",
    bodyText: "Not Found\n",
    headers: { "x-render-routing": "no-server", server: "cloudflare" },
  });
  assert.equal(verdict.kind, "permanent");
  assert.equal(verdict.code, "no-service");
});

test("a 404 on a host that DOES have a Render service is ambiguous, not missing", () => {
  const verdict = classify({
    status: 404,
    contentType: "text/plain; charset=utf-8",
    bodyText: "Not Found",
    headers: { "rndr-id": "x", "x-render-origin-server": "gunicorn" },
  });
  assert.equal(verdict.kind, "ambiguous");
  assert.match(verdict.detail, /service exists but the path is wrong/);
});

test("an unrecognised 404 body is ambiguous regardless of content type", () => {
  for (const contentType of ["text/plain", "application/octet-stream", ""]) {
    const verdict = classify({ status: 404, contentType, bodyText: "whatever" });
    assert.equal(verdict.kind, "ambiguous", `content-type ${contentType || "(none)"}`);
  }
});

test("a JSON 404 from FastAPI is permanent: the route does not exist", () => {
  const verdict = classify({
    status: 404,
    contentType: "application/json",
    bodyText: '{"detail":"Not Found"}',
  });
  assert.equal(verdict.kind, "permanent");
  assert.equal(verdict.code, "http-404");
});

test("401 and 403 are permanent configuration problems, not cold starts", () => {
  for (const status of [401, 403]) {
    const verdict = classify({ status, contentType: "application/json", bodyText: "{}" });
    assert.equal(verdict.kind, "permanent", `status ${status}`);
  }
});

test("a 200 that is not JSON means the host is not the API", () => {
  const verdict = classify({
    status: 200,
    contentType: "text/html",
    bodyText: "<!DOCTYPE html><title>Login Page</title>",
  });
  assert.equal(verdict.kind, "permanent");
  assert.equal(verdict.code, "not-json");
});

test("a 200 with malformed JSON is permanent", () => {
  const verdict = classify({ status: 200, contentType: "application/json", bodyText: "{nope" });
  assert.equal(verdict.kind, "permanent");
  assert.equal(verdict.code, "malformed-json");
});

test("a 200 JSON array, a missing list and a wrong value type are all permanent", () => {
  const asArray = classify({ status: 200, contentType: "application/json", bodyText: "[]" });
  assert.equal(asArray.kind, "permanent");

  const missing = classify({
    status: 200,
    contentType: "application/json",
    bodyText: JSON.stringify({ countries: [] }),
  });
  assert.equal(missing.kind, "permanent");
  assert.equal(missing.code, "missing-key");

  const wrongType = classify({
    status: 200,
    contentType: "application/json",
    bodyText: JSON.stringify({ countries: [], programs: "nope" }),
  });
  assert.equal(wrongType.kind, "permanent");
  assert.equal(wrongType.code, "bad-key-type");
});

test("a refused connection or a timeout is transient; an unknown error is not", () => {
  const refused = Object.assign(new Error("connect ECONNREFUSED 1.2.3.4:443"), {
    code: "ECONNREFUSED",
  });
  assert.equal(classify({ error: refused }).kind, "transient");

  const aborted = Object.assign(new Error("The operation was aborted"), { name: "AbortError" });
  assert.equal(classify({ error: aborted }).kind, "transient");

  const dns = Object.assign(new Error("getaddrinfo ENOTFOUND h"), { code: "ENOTFOUND" });
  assert.equal(classify({ error: dns }).kind, "transient");

  const nonsense = new Error("something we have never seen");
  assert.equal(classify({ error: nonsense }).kind, "permanent");
});

// ---------------------------------------------------------------- retry loop

test("a cold instance that recovers after two 503s succeeds, with growing backoff", async () => {
  const h = harness([
    () => textResponse("Service Unavailable", 503),
    () => textResponse("Service Unavailable", 503),
    () => jsonResponse(JSON_OK),
  ]);
  const result = await waitForApi({
    url: "https://api.example/api/v1/sitemap-data",
    deadlineMs: 300_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
    backoffBaseMs: 1_000,
    backoffMaxMs: 8_000,
  });
  assert.equal(result.ok, true);
  assert.equal(result.attempts, 3);
  assert.equal(h.delays.length, 2);
  assert.ok(h.delays[1] > h.delays[0], `backoff must grow: ${h.delays}`);
});

test("a permanent error stops immediately instead of burning the budget", async () => {
  const h = harness([
    () => textResponse("<!DOCTYPE html><title>Login Page</title>", 200),
  ]);
  const result = await waitForApi({
    url: "https://not-the-api.example/",
    deadlineMs: 600_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
  });
  assert.equal(result.ok, false);
  assert.equal(result.attempts, 1, "a permanent error must not be retried");
  assert.equal(result.last.code, "not-json");
  assert.equal(h.delays.length, 0);
});

test("the retry loop is bounded by the deadline and cannot spin forever", async () => {
  const h = harness([() => textResponse("Service Unavailable", 503)]);
  const result = await waitForApi({
    url: "https://api.example/api/v1/sitemap-data",
    deadlineMs: 20_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
    backoffBaseMs: 1_000,
    backoffMaxMs: 4_000,
  });
  assert.equal(result.ok, false);
  assert.ok(result.attempts > 1, "transient errors must be retried");
  assert.ok(result.attempts <= 30, `attempts must stay bounded, saw ${result.attempts}`);
  const slept = h.delays.reduce((a, b) => a + b, 0);
  assert.ok(slept <= 20_000, `total wait must respect the deadline, slept ${slept}ms`);
});

test("a confirmed missing service cannot consume the 240s cold-start window", async () => {
  // The required regression: with the *production* budget, a `no-server` answer must cost one
  // request and no waiting at all.
  const h = harness([() => renderNoServerResponse()]);
  const result = await waitForApi({
    url: "https://scholara-cfch.onrender.com/api/v1/sitemap-data",
    deadlineMs: 240_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
  });
  assert.equal(result.ok, false);
  assert.equal(result.last.code, "no-service");
  assert.equal(result.attempts, 1, "a conclusive answer must not be retried");
  assert.equal(h.delays.length, 0, "no sleep may occur before a conclusive verdict");
  assert.ok(
    h.delays.reduce((a, b) => a + b, 0) < 1_000,
    "elapsed wait must be far below the 240s cold-start budget",
  );
});

test("the no-service diagnostic is conclusive and says it is not a cold start", () => {
  const last = classify({
    status: 404,
    contentType: "text/plain",
    bodyText: "Not Found\n",
    headers: { "x-render-routing": "no-server" },
  });
  const text = failureLines({ base: "https://scholara-cfch.onrender.com", last, deadlineMs: 240_000 }).join("\n");
  assert.match(text, /x-render-routing: no-server/);
  assert.match(text, /not a cold start/);
  assert.match(text, /no service is attached to this hostname/);
  // It must not pretend the build waited out a budget it never spent.
  assert.doesNotMatch(text, /within 240s/);
});

test("an ambiguous 404 is bounded and cannot run down the 240s budget either", async () => {
  const h = harness([() => textResponse("Not Found", 404)]);
  const result = await waitForApi({
    url: "https://unknown-host.example/api/v1/sitemap-data",
    deadlineMs: 240_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
  });
  assert.equal(result.ok, false);
  assert.equal(result.last.kind, "ambiguous");
  assert.ok(
    result.attempts <= AMBIGUOUS_MAX_ATTEMPTS,
    `ambiguous attempts must be capped at ${AMBIGUOUS_MAX_ATTEMPTS}, saw ${result.attempts}`,
  );
  const slept = h.delays.reduce((a, b) => a + b, 0);
  assert.ok(
    slept <= AMBIGUOUS_BUDGET_MS,
    `ambiguous wait must stay under its own ${AMBIGUOUS_BUDGET_MS}ms budget, slept ${slept}ms`,
  );
});

test("the ambiguous diagnostic claims neither sleep nor definitive absence", () => {
  const last = classify({ status: 404, contentType: "text/html", bodyText: "<html>404</html>" });
  assert.equal(last.kind, "ambiguous");
  const text = failureLines({
    base: "https://unknown-host.example",
    last,
    deadlineMs: 240_000,
    attempts: 3,
    elapsedMs: 4_500,
  }).join("\n");
  assert.match(text, /ambiguous/i);
  assert.match(text, /NOT being called a cold start/i);
  assert.match(text, /cannot tell whether a service exists/i);
  // Never assert a cause the evidence does not support.
  assert.doesNotMatch(text, /is asleep|merely cold|sleeping service/i);
  assert.doesNotMatch(text, /conclusively|definitively/i);
  // Any statement about absence must be conditional operator guidance, never a verdict.
  assert.match(text, /if it returns x-render-routing: no-server,/);
});

test("Campaign 11's transient retry behaviour is unchanged (503 still uses the budget)", async () => {
  // Regression guard: the correction must not shrink the cold-start window for real transient
  // conditions. A 503 must still be retried well past the ambiguous cap.
  const h = harness([() => textResponse("Service Unavailable", 503)]);
  const result = await waitForApi({
    url: "https://api.example/api/v1/sitemap-data",
    deadlineMs: 120_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
    backoffBaseMs: 1_000,
    backoffMaxMs: 8_000,
  });
  assert.equal(result.ok, false);
  assert.equal(result.last.kind, "transient");
  assert.ok(
    result.attempts > AMBIGUOUS_MAX_ATTEMPTS,
    `a 503 must be retried beyond the ambiguous cap, saw ${result.attempts} attempts`,
  );
  const slept = h.delays.reduce((a, b) => a + b, 0);
  assert.ok(slept > AMBIGUOUS_BUDGET_MS, `a 503 must outlast the ambiguous budget, slept ${slept}ms`);
});

test("neither new diagnostic branch leaks a credential from the origin", () => {
  for (const last of [
    classify({ status: 404, contentType: "text/plain", bodyText: "Not Found", headers: { "x-render-routing": "no-server" } }),
    classify({ status: 404, contentType: "text/html", bodyText: "nope" }),
  ]) {
    const text = failureLines({ base: "https://user:hunter2@api.example.com", last, deadlineMs: 1000 }).join("\n");
    assert.doesNotMatch(text, /hunter2/);
    assert.doesNotMatch(text, /user:/);
  }
});

test("an ambiguous answer is never reported as ready and never fabricates counts", async () => {
  const h = harness([() => textResponse("Not Found", 404)]);
  const result = await waitForApi({
    url: "https://unknown-host.example/api/v1/sitemap-data",
    deadlineMs: 240_000,
    fetchImpl: h.fetchImpl,
    sleepImpl: h.sleepImpl,
    nowImpl: h.nowImpl,
  });
  assert.equal(result.ok, false);
  assert.equal(result.counts, undefined);
  assert.equal(result.total, undefined);
});

test("probeOnce carries Render's routing header through to the classifier", async () => {
  // Proves the wiring, not just the classifier: the verdict depends on a header that must
  // survive the fetch, so a unit test of `classify` alone would not catch a dropped header.
  const noServer = await probeOnce({
    url: "https://scholara-cfch.onrender.com/api/v1/sitemap-data",
    fetchImpl: (async () => renderNoServerResponse()) as unknown as typeof fetch,
  });
  assert.equal(noServer.kind, "permanent");
  assert.equal(noServer.code, "no-service");

  const hasService = await probeOnce({
    url: "https://api.example/api/v1/sitemap-data",
    fetchImpl: (async () => renderServiceMissingRouteResponse()) as unknown as typeof fetch,
  });
  assert.equal(hasService.kind, "ambiguous");
  assert.match(hasService.detail, /service exists but the path is wrong/);
});

test("probeOnce sends the accept header and never throws", async () => {
  const captured: Array<Record<string, string>> = [];
  await probeOnce({
    url: "https://api.example/x",
    fetchImpl: (async (_url: string, init: RequestInit) => {
      captured.push(init.headers as Record<string, string>);
      return jsonResponse(JSON_OK);
    }) as unknown as typeof fetch,
  });
  assert.equal(captured[0].Accept, "application/json");
});

// ---------------------------------------------------------------- redaction

test("redactUrl strips credentials and the query string", () => {
  assert.equal(
    redactUrl("https://user:supersecret@api.example.com/api/v1/sitemap-data?token=abc123"),
    "https://***@api.example.com/api/v1/sitemap-data",
  );
  assert.equal(redactUrl("https://api.example.com/"), "https://api.example.com/");
  assert.equal(redactUrl("not a url"), "(unparseable URL)");
  assert.equal(redactUrl(""), "(empty)");
});

test("the failure text never contains a credential from the origin", () => {
  const last = classify({ status: 404, contentType: "text/html", bodyText: "Not Found" });
  const text = failureLines({
    base: "https://user:hunter2@api.example.com",
    last,
    deadlineMs: 1000,
  }).join("\n");
  assert.doesNotMatch(text, /hunter2/);
  assert.doesNotMatch(text, /user:/);
});

test("describeCounts renders the operator-facing summary", () => {
  assert.equal(describeCounts({ countries: 2, programs: 5 }), "countries=2 programs=5");
  assert.equal(describeCounts({}), "(no lists)");
});
