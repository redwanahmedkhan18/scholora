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
  const sp = new URLSearchParams(window.location.search);
  const keys = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid"];
  const found: Dict = {};
  keys.forEach((k) => { const v = sp.get(k); if (v) found[k] = v; });
  if (Object.keys(found).length) {
    found.landing_path = window.location.pathname;
    found.captured_at = new Date().toISOString();
    window.sessionStorage.setItem(UTM_KEY, JSON.stringify(found));
    return found;
  }
  try { return JSON.parse(window.sessionStorage.getItem(UTM_KEY) ?? "{}"); } catch { return {}; }
}

export function storedUtm(): Dict {
  if (typeof window === "undefined") return {};
  try { return JSON.parse(window.sessionStorage.getItem(UTM_KEY) ?? "{}"); } catch { return {}; }
}

/** One call fans out to the first-party API, GA4 and the Meta Pixel. */
export function track(event: string, payload: Dict = {}, pixelEvent?: string) {
  if (typeof window === "undefined") return;
  const body = {
    name: event,
    path: window.location.pathname,
    utm: storedUtm(),
    meta: { ...payload, session_id: sessionId() },
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

  window.gtag?.("event", event, payload);
  if (pixelEvent) window.fbq?.("track", pixelEvent, payload);
}

export function whatsappHref(number?: string | null, message?: string, context?: string) {
  const digits = (number ?? "").replace(/[^\d]/g, "");
  const text = encodeURIComponent(
    [message ?? "Hello Scholara, I would like to discuss studying abroad.", context].filter(Boolean).join(" "),
  );
  return `https://wa.me/${digits}?text=${text}`;
}
