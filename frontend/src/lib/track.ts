"use client";

type Dict = Record<string, any>;

declare global {
  interface Window {
    dataLayer?: any[];
    gtag?: (...args: any[]) => void;
    fbq?: (...args: any[]) => void;
  }
}

const SESSION_KEY = "scholara.sid";
const UTM_KEY = "scholara.utm";

/**
 * The portal link is a bearer capability. Paths below `/my` must never enter analytics,
 * third-party pixels, UTM landing-path storage or the first-party analytics table.
 * Query strings and fragments are discarded so a token cannot ride along there.
 */
export function sanitizeAnalyticsPath(pathname: string | null | undefined): string | null {
  const raw = (pathname ?? "").trim();
  if (!raw) return null;
  let path = raw;
  try {
    if (/^https?:\/\//i.test(raw)) path = new URL(raw).pathname;
    else path = raw.split(/[?#]/, 1)[0];
  } catch {
    return null;
  }
  if (!path.startsWith("/")) return null;
  if (path === "/my" || path.startsWith("/my/")) return null;
  return path;
}

function scrubValue(value: any): any {
  if (typeof value === "string") return value.replace(/\/my\/[^\s\"?#]*/g, "/my");
  if (Array.isArray(value)) return value.map(scrubValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, scrubValue(v)]));
  }
  return value;
}

function safePayload(payload: Dict): Dict {
  const out = scrubValue({ ...payload });
  if ("path" in out) {
    const safe = sanitizeAnalyticsPath(out.path);
    if (safe === null) delete out.path;
    else out.path = safe;
  }
  return out;
}

function safeStoredUtm(value: Dict): Dict {
  const out = { ...value };
  if ("landing_path" in out) {
    const safe = sanitizeAnalyticsPath(out.landing_path);
    if (safe === null) delete out.landing_path;
    else out.landing_path = safe;
  }
  return out;
}

export function sessionId(): string {
  if (typeof window === "undefined") return "server";
  let id = window.localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = `s_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
    window.localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

/** Capture UTM parameters once per visit so every lead can be attributed to a campaign. */
export function captureUtm(): Dict {
  if (typeof window === "undefined") return {};
  // Do not create or retain campaign metadata on a private portal route. Remove any legacy
  // entry-path value that may still contain a capability from an earlier deployment.
  if (sanitizeAnalyticsPath(window.location.pathname) === null) {
    try { window.sessionStorage.removeItem(UTM_KEY); } catch { /* storage unavailable */ }
    return {};
  }
  const sp = new URLSearchParams(window.location.search);
  const keys = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid"];
  const found: Dict = {};
  keys.forEach((k) => { const v = sp.get(k); if (v) found[k] = v; });
  if (Object.keys(found).length) {
    const safeLanding = sanitizeAnalyticsPath(window.location.pathname);
    if (safeLanding === null) return {};
    found.landing_path = safeLanding;
    found.captured_at = new Date().toISOString();
    window.sessionStorage.setItem(UTM_KEY, JSON.stringify(found));
    return found;
  }
  try { return safeStoredUtm(JSON.parse(window.sessionStorage.getItem(UTM_KEY) ?? "{}")); }
  catch { return {}; }
}

export function storedUtm(): Dict {
  if (typeof window === "undefined") return {};
  try {
    const raw = JSON.parse(window.sessionStorage.getItem(UTM_KEY) ?? "{}");
    const safe = safeStoredUtm(raw);
    if (JSON.stringify(raw) !== JSON.stringify(safe)) window.sessionStorage.removeItem(UTM_KEY);
    return safe;
  } catch { return {}; }
}

/** One call fans out to the first-party API, GA4 and the Meta Pixel. */
export function track(event: string, payload: Dict = {}, pixelEvent?: string) {
  if (typeof window === "undefined") return;
  const currentPath = sanitizeAnalyticsPath(window.location.pathname);
  if (currentPath === null) return;
  const safe = safePayload(payload);
  const body = {
    name: event,
    path: currentPath,
    utm: storedUtm(),
    meta: { ...safe, session_id: sessionId() },
  };
  try {
    navigator.sendBeacon?.(
      "/api/v1/track",
      new Blob([JSON.stringify(body)], { type: "application/json" }),
    ) ||
      fetch("/api/v1/track", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive: true,
      }).catch(() => {});
  } catch { /* analytics must never break the page */ }

  window.gtag?.("event", event, safe);
  if (pixelEvent) window.fbq?.("track", pixelEvent, safe);
}

export function whatsappHref(number?: string | null, message?: string, context?: string) {
  const digits = (number ?? "").replace(/[^\d]/g, "");
  const text = encodeURIComponent(
    [message ?? "Hello Scholara, I would like to discuss studying abroad.", context].filter(Boolean).join(" "),
  );
  return `https://wa.me/${digits}?text=${text}`;
}
