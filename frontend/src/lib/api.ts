/** Server-side data access. Browser code must use relative "/api/..." instead. */
const BASE = process.env.INTERNAL_API_URL ?? "http://127.0.0.1:8000";

export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export type Dict = Record<string, any>;

export async function api<T = Dict>(path: string, revalidate = 60): Promise<T> {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    next: { revalidate },
    headers: { Accept: "application/json" },
  });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return (await res.json()) as T;
}

/** Returns null on 404 instead of throwing, for notFound() handling. */
/**
 * Fetch a resource, returning null ONLY when the API says it genuinely does not exist.
 *
 * The distinction matters more than it looks. Callers turn `null` into notFound(), and at
 * build time that answer is frozen into a static page. So if a transport error or a 429
 * were allowed to look like a 404, a throttled build would publish a permanent "not found"
 * page for a university that exists - which is exactly what happened once. Anything that is
 * not a clean 404 therefore throws, so the build fails loudly instead of lying quietly.
 */
export async function apiOrNull<T = Dict>(path: string, revalidate = 60): Promise<T | null> {
  const url = `${BASE}/api/v1${path}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  // Server-side only: exempts first-party rendering from the public rate limit. `window`
  // is undefined during SSR/SSG; in the browser this header is never attached.
  if (typeof window === "undefined" && process.env.INTERNAL_API_TOKEN) {
    headers["X-Internal-Token"] = process.env.INTERNAL_API_TOKEN;
  }

  let res: Response;
  try {
    res = await fetch(url, { next: { revalidate }, headers });
  } catch (cause) {
    throw new Error(`Cannot reach the API for ${path}`, { cause });
  }

  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(
      `API returned ${res.status} for ${path}` +
        (res.status === 429 ? " - rate limited; set INTERNAL_API_TOKEN for builds" : ""),
    );
  }
  return (await res.json()) as T;
}

/**
 * The public /settings endpoint groups fields (whatsapp.*, facebook.*, social.*, marketing.*,
 * seo.*, rotation.*). Components read a flat shape, so normalise once here — grouped keys are
 * kept too, so either access style works.
 */
function flattenSettings(raw: Dict): Dict {
  const s = raw ?? {};
  const wa = s.whatsapp ?? {};
  const fb = s.facebook ?? {};
  const so = s.social ?? {};
  const mk = s.marketing ?? {};
  const seo = s.seo ?? {};
  const rot = s.rotation ?? {};

  /** Treat obvious placeholders (G-XXXXXXXXXX, 000000000000000, AW-XXXXXXXXX) as "not configured". */
  const real = (v?: string | null) => {
    if (!v) return undefined;
    const t = String(v).trim();
    if (!t || /X{4,}/i.test(t) || /^0+$/.test(t.replace(/\D/g, "")) === true) return undefined;
    return t;
  };

  return {
    ...s,
    whatsapp_enabled: wa.enabled ?? true,
    whatsapp_number: wa.number ?? null,
    whatsapp_default_message: wa.default_message ?? "",

    facebook_page_url: fb.page_url ?? null,
    facebook_page_id: fb.page_id ?? null,
    facebook_page_plugin_enabled: fb.page_plugin_enabled ?? false,
    messenger_url: fb.messenger_url ?? null,
    messenger_enabled: fb.messenger_enabled ?? false,

    instagram_url: so.instagram ?? null,
    linkedin_url: so.linkedin ?? null,
    youtube_url: so.youtube ?? null,
    tiktok_url: so.tiktok ?? null,
    x_url: so.x ?? null,

    ga4_measurement_id: real(mk.ga4_measurement_id),
    gtm_container_id: real(mk.gtm_container_id),
    google_ads_conversion_id: real(mk.google_ads_conversion_id),
    google_ads_conversion_label: real(mk.google_ads_conversion_label),
    meta_pixel_id: real(mk.meta_pixel_id),
    tiktok_pixel_id: real(mk.tiktok_pixel_id),
    analytics_consent_required: mk.consent_required ?? true,

    google_site_verification: real(seo.google_site_verification),
    meta_domain_verification: real(seo.meta_domain_verification),
    bing_verification: real(seo.bing_verification),
    default_seo_title: seo.default_title ?? s.brand_name,
    default_seo_description: seo.default_description ?? s.tagline,
    default_og_image: seo.default_og_image ?? null,
    site_url: seo.site_url ?? SITE_URL,
    organization_type: seo.organization_type ?? "EducationalOrganization",
    founding_year: seo.founding_year ?? null,

    hero_rotation_seconds: rot.hero_seconds ?? 30,
    hero_rotation_enabled: rot.hero_enabled ?? true,
    hero_time_scheduling_enabled: rot.time_scheduling ?? false,
    story_rotation_seconds: rot.story_seconds ?? 12,
  };
}

export async function getSettings(): Promise<Dict> {
  return flattenSettings((await apiOrNull<Dict>("/settings", 300)) ?? {});
}

/**
 * Controlled vocabularies (degree levels, confidence/eligibility states, pipeline stages).
 * These are defined once in the backend's app/models/base.py and served read-only, so the
 * UI never re-declares a list the engines also use. Cached for an hour: they change only
 * with a deploy, unlike /settings which an admin edits live.
 */
export async function getVocabularies(): Promise<Dict> {
  return (await apiOrNull<Dict>("/vocabularies", 3600)) ?? {};
}

/** Pull one vocabulary as {value,label}[], tolerating an unreachable API. */
export async function getOptions(name: string): Promise<Array<{ value: string; label: string }>> {
  const all = await getVocabularies();
  return (all[name] as Array<{ value: string; label: string }>) ?? [];
}

export function qs(params: Dict): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "" || v === "all") return;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.append(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}
