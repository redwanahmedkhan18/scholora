/**
 * Deciding whether the backend is ready for a production build to consume.
 *
 * WHY THIS IS A MODULE AND NOT TEN LINES INSIDE THE SCRIPT
 * --------------------------------------------------------
 * The prebuild step is the only thing standing between a sleeping Render instance and a
 * failed deploy, and it is also the only thing standing between a *misconfigured* backend
 * origin and a build that publishes empty pages. Those two situations must be told apart,
 * because they need opposite responses:
 *
 *   * a cold instance answers nothing, or 502/503/504, and then becomes healthy - waiting is
 *     correct and a bounded exponential backoff will get there;
 *   * a wrong `INTERNAL_API_URL`, a suspended service or an authentication error answers 404,
 *     401, or an HTML page, and waiting can never fix it - the build must fail *immediately*
 *     with a message that names the real problem instead of burning the whole budget and then
 *     claiming "the instance is merely cold".
 *
 * THREE-WAY, NOT TWO-WAY (Campaign 11A)
 * --------------------------------------
 * A 404 with no JSON body is not one condition, so it does not get one verdict. Render's routing
 * layer states the answer in a response header:
 *
 *   x-render-routing: no-server   -> no service is attached to this hostname. Render is saying,
 *                                    in its own words, that nothing is behind the address. That
 *                                    is permanent: fail at once, never spend the cold-start
 *                                    budget on it.
 *   rndr-id / x-render-origin-server present, but the body is not JSON
 *                                 -> a service *does* exist; the route or the app is wrong.
 *   no Render header at all       -> something answered 404 and we cannot tell what. That is
 *                                    genuinely ambiguous and is reported as such, after a short
 *                                    bounded retry, never as "asleep" and never as "gone".
 *
 * The previous version collapsed all three into `transient`/`edge-404` and retried for the full
 * budget, which is the opposite of what its own final message claimed and is wrong on the
 * evidence: `scholara-cfch.onrender.com` (note: with an "a") answers `404 / text/plain /
 * 10 bytes / x-render-routing: no-server` in ~0.4 s, byte-for-byte identical to a hostname that
 * certainly has no service - while a live Render service answers with `rndr-id` and
 * `x-render-origin-server`. A sleeping instance still has a server; `no-server` is not sleep.
 * The live Scholara backend is the same name with an "o": `scholora-cfch.onrender.com`.
 * That single character is precisely the trap this classification exists to catch.
 *
 * The previous version retried every non-2xx for the full budget, so a permanently wrong URL
 * looked exactly like a slow start. Keeping the classifier here, with injectable clock and
 * fetch, is what lets the behaviour be tested without a network or a real wait.
 *
 * Everything here is dependency-free so it can run in a bare `node scripts/*.mjs` step.
 */

/** The keys `GET /api/v1/sitemap-data` is contracted to return as arrays. */
export const SITEMAP_KEYS = Object.freeze([
  "countries",
  "universities",
  "programs",
  "scholarships",
  "faculty",
  "faculties",
  "services",
  "success_stories",
  "events",
  "posts",
]);

/** Keys that must be present and be arrays; the rest are optional but must be arrays if present. */
const REQUIRED_KEYS = Object.freeze(["countries", "programs"]);

/** Statuses that mean "the platform is up but the app behind it is not serving yet". */
const TRANSIENT_STATUS = new Set([429, 502, 503, 504]);

/** Statuses that mean "this will never work without a configuration change". */
const PERMANENT_STATUS = new Set([400, 401, 403, 404, 405, 422]);

/**
 * Render states the routing outcome in a response header. `no-server` means no service is
 * attached to this hostname - the platform's own answer, not an inference from a body shape.
 */
export const RENDER_ROUTING_HEADER = "x-render-routing";

/** Headers that only a live Render service emits, so they prove a service exists. */
const RENDER_SERVICE_HEADERS = Object.freeze(["rndr-id", "x-render-origin-server"]);

/**
 * An ambiguous answer gets its own, deliberately short budget. It must never be allowed to run
 * down the 240 s cold-start window, because the whole point is that we cannot say a wait helps.
 */
export const AMBIGUOUS_MAX_ATTEMPTS = 3;
export const AMBIGUOUS_BUDGET_MS = 20_000;

/**
 * The `x-render-routing` value, lowercased, or `""` when the header is absent.
 *
 * @param {Record<string, string> | undefined} headers
 * @returns {string}
 */
export function renderRoutingOf(headers) {
  const raw = headers ? headers[RENDER_ROUTING_HEADER] : undefined;
  return typeof raw === "string" ? raw.trim().toLowerCase() : "";
}

/**
 * True when the response proves a Render service is attached to the host.
 *
 * @param {Record<string, string> | undefined} headers
 * @returns {boolean}
 */
export function hasRenderService(headers) {
  if (!headers) return false;
  return RENDER_SERVICE_HEADERS.some((name) => Boolean(headers[name]));
}

/**
 * Remove anything from a URL that must not reach a build log: userinfo (a password in
 * `https://user:secret@host`) and the whole query string, which is where signed-URL and
 * token parameters live. The scheme, host and path are all an operator needs to diagnose.
 *
 * @param {string} raw
 * @returns {string}
 */
export function redactUrl(raw) {
  const text = typeof raw === "string" ? raw.trim() : "";
  if (!text) return "(empty)";
  try {
    const url = new URL(text);
    const auth = url.username || url.password ? "***@" : "";
    return `${url.protocol}//${auth}${url.host}${url.pathname}`;
  } catch {
    // Not a URL. Never echo an unparseable value: it could be a half-pasted secret.
    return "(unparseable URL)";
  }
}

/**
 * Classify one probe result.
 *
 * @param {{ status?: number, contentType?: string, bodyText?: string, error?: unknown,
 *           headers?: Record<string, string> }} input
 * @returns {{ kind: "ready" | "transient" | "ambiguous" | "permanent", code: string,
 *             detail: string, counts?: Record<string, number>, total?: number, body?: unknown }}
 */
export function classify(input) {
  const error = input.error;
  if (error) {
    const name = error && error.name ? String(error.name) : "";
    const message = error && error.message ? String(error.message).slice(0, 200) : String(error);
    const code = error && error.code ? String(error.code) : "";
    // A timeout or a refused/reset connection is the signature of a sleeping or booting
    // instance, and of nothing else that a wait cannot fix.
    const transientSignals = [
      "AbortError",
      "TimeoutError",
      "ECONNREFUSED",
      "ECONNRESET",
      "ENOTFOUND",
      "EAI_AGAIN",
      "ETIMEDOUT",
      "EHOSTUNREACH",
      "ENETUNREACH",
      "UND_ERR_CONNECT_TIMEOUT",
      "UND_ERR_HEADERS_TIMEOUT",
      "UND_ERR_SOCKET",
      "EPIPE",
    ];
    const label = `${name} ${code} ${message}`;
    const transient = transientSignals.some((signal) => label.includes(signal));
    return {
      kind: transient ? "transient" : "permanent",
      code: transient ? "transport" : "unexpected-error",
      detail: `${name || "Error"}${code ? ` (${code})` : ""}: ${message}`,
    };
  }

  const status = Number(input.status ?? 0);
  const contentType = String(input.contentType ?? "").toLowerCase();
  const bodyText = String(input.bodyText ?? "");
  const isJson = contentType.includes("application/json");
  const snippet = bodyText.replace(/\s+/g, " ").slice(0, 160);

  // A gateway status is exactly what Render's edge and a restarting instance emit.
  if (TRANSIENT_STATUS.has(status)) {
    return {
      kind: "transient",
      code: `http-${status}`,
      detail: `HTTP ${status}${snippet ? `: ${snippet}` : ""}`,
    };
  }

  if (status === 404 && !isJson) {
    // Render answers "nothing is behind this address" in a header, not in a body shape. When it
    // says so, that verdict is final: no amount of waiting attaches a service to a hostname.
    if (renderRoutingOf(input.headers) === "no-server") {
      return {
        kind: "permanent",
        code: "no-service",
        detail:
          "HTTP 404 with x-render-routing: no-server - Render reports that no service is " +
          "attached to this hostname",
      };
    }

    // Everything else 404-without-JSON is a genuine unknown. A service may exist and be
    // misrouted (Render's own service headers present), or the 404 may come from some other
    // proxy we know nothing about. We must not call this a cold start, and we must not call it
    // a missing service either - so it is reported as ambiguous after a short bounded retry.
    return {
      kind: "ambiguous",
      code: "edge-404-unconfirmed",
      detail:
        `HTTP 404 without a JSON body and without Render's no-server marker` +
        (hasRenderService(input.headers)
          ? " (Render service headers are present, so the service exists but the path is wrong)"
          : " (no Render routing header, so the origin may not be Render at all)"),
    };
  }

  if (PERMANENT_STATUS.has(status)) {
    return {
      kind: "permanent",
      code: `http-${status}`,
      detail:
        `HTTP ${status}${snippet ? `: ${snippet}` : ""}` +
        (status === 404 ? " (the API answered, but not at this path)" : ""),
    };
  }

  if (status < 200 || status >= 300) {
    // Any other 5xx is still worth waiting on; any other 4xx is not.
    const transient = status >= 500;
    return {
      kind: transient ? "transient" : "permanent",
      code: `http-${status}`,
      detail: `HTTP ${status}${snippet ? `: ${snippet}` : ""}`,
    };
  }

  if (!isJson) {
    return {
      kind: "permanent",
      code: "not-json",
      detail:
        `HTTP ${status} with content-type ${contentType || "(none)"} - this host is not the ` +
        `API. Check INTERNAL_API_URL points at the Render web service (not a static site).`,
    };
  }

  let body;
  try {
    body = JSON.parse(bodyText);
  } catch (cause) {
    return {
      kind: "permanent",
      code: "malformed-json",
      detail: `HTTP ${status} with a body that is not valid JSON: ${String(cause).slice(0, 120)}`,
    };
  }

  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return {
      kind: "permanent",
      code: "bad-shape",
      detail: `HTTP ${status} returned ${Array.isArray(body) ? "an array" : typeof body}, ` +
        `expected a JSON object of sitemap lists`,
    };
  }

  for (const key of REQUIRED_KEYS) {
    if (!(key in body)) {
      return {
        kind: "permanent",
        code: "missing-key",
        detail: `HTTP ${status} is JSON but has no "${key}" list - wrong endpoint or API version`,
      };
    }
  }
  for (const [key, value] of Object.entries(body)) {
    if (SITEMAP_KEYS.includes(key) && !Array.isArray(value)) {
      return {
        kind: "permanent",
        code: "bad-key-type",
        detail: `HTTP ${status}: "${key}" is ${typeof value}, expected a list`,
      };
    }
  }

  const counts = {};
  let total = 0;
  for (const key of SITEMAP_KEYS) {
    if (Array.isArray(body[key])) {
      counts[key] = body[key].length;
      total += body[key].length;
    }
  }
  return {
    kind: "ready",
    code: "ok",
    detail: `HTTP ${status}`,
    counts,
    total,
    body,
  };
}

/**
 * One probe: fetch, read the body once, classify. Never throws.
 *
 * @param {{ url: string, fetchImpl?: typeof fetch, timeoutMs?: number, headers?: Record<string,string> }} opts
 */
export async function probeOnce(opts) {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const timeoutMs = opts.timeoutMs ?? 25_000;
  const headers = { Accept: "application/json", ...(opts.headers ?? {}) };
  try {
    // `AbortSignal.timeout` is the only deadline mechanism used here, and it is missing on Node
    // builds older than 17.3. Without this guard that gap would surface as an `unexpected-error`
    // and fail an otherwise healthy build, so the request is simply left unbounded and the
    // caller's deadline still applies.
    const signal =
      typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function"
        ? AbortSignal.timeout(timeoutMs)
        : undefined;
    const res = await fetchImpl(opts.url, {
      headers,
      ...(signal ? { signal } : {}),
      cache: "no-store",
    });
    const bodyText = await res.text();
    // Only the routing outcome is carried forward: it is the platform's own statement about the
    // hostname, and it is the one header that changes a verdict. Nothing else is kept, so no
    // unrelated header can leak into a log or a classifier decision.
    const routingHeaders = {};
    for (const name of [RENDER_ROUTING_HEADER, ...RENDER_SERVICE_HEADERS]) {
      const value = res.headers.get(name);
      if (value) routingHeaders[name] = value;
    }
    return classify({
      status: res.status,
      contentType: res.headers.get("content-type") ?? "",
      bodyText,
      headers: routingHeaders,
    });
  } catch (error) {
    return classify({ error });
  }
}

/**
 * Poll the backend until it can serve sitemap data, or until a permanent error or the
 * deadline stops us.
 *
 * @param {{
 *   url: string,
 *   deadlineMs: number,
 *   fetchImpl?: typeof fetch,
 *   sleepImpl?: (ms: number) => Promise<void>,
 *   nowImpl?: () => number,
 *   timeoutMs?: number,
 *   headers?: Record<string, string>,
 *   backoffBaseMs?: number,
 *   backoffMaxMs?: number,
 *   log?: (line: string) => void,
 * }} opts
 * @returns {Promise<{ ok: boolean, attempts: number, last: ReturnType<typeof classify>,
 *                     counts?: Record<string, number>, total?: number }>}
 */
export async function waitForApi(opts) {
  const sleepImpl = opts.sleepImpl ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  const nowImpl = opts.nowImpl ?? (() => Date.now());
  const log = opts.log ?? (() => {});
  const backoffBaseMs = opts.backoffBaseMs ?? 1_500;
  const backoffMaxMs = opts.backoffMaxMs ?? 15_000;
  const started = nowImpl();
  const deadline = started + Math.max(0, opts.deadlineMs);

  let attempts = 0;
  let last = classify({ status: 0, contentType: "", bodyText: "" });
  // An ambiguous answer starts a *separate*, short clock. Using one clock for both would let an
  // unclassifiable response sit inside a 240 s cold-start budget, which is exactly the defect
  // this replaces.
  let ambiguousDeadline = Infinity;
  const ambiguousCapMs = opts.ambiguousBudgetMs ?? AMBIGUOUS_BUDGET_MS;
  const ambiguousMaxAttempts = opts.ambiguousMaxAttempts ?? AMBIGUOUS_MAX_ATTEMPTS;

  while (true) {
    attempts += 1;
    last = await probeOnce({
      url: opts.url,
      fetchImpl: opts.fetchImpl,
      timeoutMs: opts.timeoutMs,
      headers: opts.headers,
    });

    if (last.kind === "ready") {
      log(`API ready after ${attempts} attempt(s): ${describeCounts(last.counts ?? {})}`);
      return { ok: true, attempts, last, counts: last.counts, total: last.total };
    }
    if (last.kind === "permanent") {
      // Waiting cannot change a configuration error, and every extra attempt makes the log
      // harder to read and the deploy slower to fail.
      log(`attempt ${attempts}: permanent (${last.code}) ${last.detail}`);
      return { ok: false, attempts, last };
    }

    let effectiveDeadline = deadline;
    if (last.kind === "ambiguous") {
      if (ambiguousDeadline === Infinity) ambiguousDeadline = nowImpl() + ambiguousCapMs;
      log(`attempt ${attempts}: ambiguous (${last.code}) ${last.detail}`);
      if (attempts >= ambiguousMaxAttempts || nowImpl() >= ambiguousDeadline) {
        return { ok: false, attempts, last };
      }
      effectiveDeadline = Math.min(deadline, ambiguousDeadline);
    } else {
      log(`attempt ${attempts}: transient (${last.code}) ${last.detail}`);
    }

    const delay = Math.min(backoffMaxMs, backoffBaseMs * 2 ** (attempts - 1));
    const jitter = 0.85 + Math.random() * 0.3;
    const wait = Math.round(delay * jitter);
    if (nowImpl() + wait >= effectiveDeadline) break;
    await sleepImpl(wait);
    if (nowImpl() >= effectiveDeadline) break;
  }
  return { ok: false, attempts, last };
}

/**
 * "countries=12 universities=91 …" - the shape an operator reads in a build log.
 *
 * @param {Record<string, number>} counts
 * @returns {string}
 */
export function describeCounts(counts) {
  const parts = Object.entries(counts).map(([key, n]) => `${key}=${n}`);
  return parts.length ? parts.join(" ") : "(no lists)";
}

/**
 * The message printed when the probe gives up. It names the *observed* condition instead of
 * guessing, because the previous "the instance is merely cold" line was wrong for the most
 * common real cause: a wrong or suspended origin.
 *
 * @param {{ base: string, last: ReturnType<typeof classify>, deadlineMs: number,
 *           attempts?: number, elapsedMs?: number }} input
 * @returns {string[]} lines, already redacted
 */
export function failureLines({ base, last, deadlineMs, attempts, elapsedMs }) {
  const spent =
    typeof elapsedMs === "number"
      ? `${(elapsedMs / 1000).toFixed(1)}s`
      : `up to ${Math.round(deadlineMs / 1000)}s`;
  const tried = typeof attempts === "number" ? `${attempts} attempt(s)` : "repeated attempts";
  const lines = [
    `[warm-api] the API at ${redactUrl(base)} could not be verified as ready after ${tried} (${spent}).`,
    `[warm-api] last result: ${last.code} - ${last.detail}`,
  ];
  if (last.code === "no-service") {
    lines.push(
      "",
      "[warm-api] This is conclusive, and it is not a cold start. Render's own routing header says",
      "`x-render-routing: no-server`, which means no service is attached to this hostname. A sleeping",
      "instance still has a server - it would answer 502/503 or take tens of seconds to wake. Nothing",
      "behind this address will ever answer, so the build stopped instead of waiting out its budget.",
      "  1. open the Render dashboard and find the web service that runs this repository; note its",
      "     exact onrender.com origin (or attach a custom domain);",
      "  2. point INTERNAL_API_URL in the Vercel project at that origin and redeploy;",
      "  3. confirm GET <origin>/api/health/live returns 200 before rebuilding.",
    );
  } else if (last.code === "edge-404-unconfirmed") {
    lines.push(
      "",
      "[warm-api] This answer is ambiguous and is deliberately NOT being called a cold start.",
      "The host returned 404 without a JSON body, and without Render's `x-render-routing` marker,",
      "so the build cannot tell whether a service exists behind it. It retried briefly and stopped",
      "rather than spend the full cold-start budget on a guess.",
      "  Verify, in this order:",
      "  1. `curl -i <INTERNAL_API_URL>/api/health/live` - if it returns x-render-routing: no-server,",
      "     the service is absent (suspended, renamed or deleted) and INTERNAL_API_URL is wrong;",
      "  2. if it returns a live Render service but 404, the path or API version is wrong;",
      "  3. if the response has no Render headers at all, the host in INTERNAL_API_URL is not a",
      "     Render service - check for a typo, a stale domain or a proxy in front of the API.",
    );
  } else if (last.code === "http-401" || last.code === "http-403") {
    lines.push(
      "",
      "[warm-api] The API refused the build's credentials. /api/v1/sitemap-data is public, so this",
      "means a proxy or an incorrect origin is in front of it. Check INTERNAL_API_URL, and that",
      "INTERNAL_API_TOKEN in Vercel matches the backend's value.",
    );
  } else if (["not-json", "bad-shape", "missing-key", "bad-key-type", "http-404"].includes(last.code)) {
    lines.push(
      "",
      "[warm-api] The host answered, but not as Scholara's API. This is a configuration error,",
      "not a cold start: confirm INTERNAL_API_URL is the Render web service origin and that the",
      "service is deployed from this repository.",
    );
  } else {
    lines.push(
      "",
      "[warm-api] The service looked asleep or still booting the whole time. Confirm the Render",
      "service is Live, that DATABASE_URL is the Supabase Session Pooler (IPv4) URL, and that",
      "Render's health check path is /api/health/live. Raise API_WARMUP_TIMEOUT_SECONDS if the",
      "instance is genuinely just cold.",
    );
  }
  lines.push(
    "",
    "The build needs live data for prerendering, and failing here is better than publishing",
    "empty pages.",
  );
  return lines;
}
