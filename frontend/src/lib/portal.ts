"use client";

/** Student-portal client helpers: token storage, API access and shortlist sync. */

type Dict = Record<string, any>;

const TOKEN_KEY = "scholara.profile.token";
const NAME_KEY = "scholara.profile.name";
const SHORTLIST_KEY = "scholara.shortlist";

export function getProfileToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getProfileName(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(NAME_KEY);
}

export function setProfileToken(token: string, name?: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
  if (name) window.localStorage.setItem(NAME_KEY, name);
  window.dispatchEvent(new Event("scholara:profile"));
}

export function clearProfileToken() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(NAME_KEY);
  window.dispatchEvent(new Event("scholara:profile"));
}

export async function portalFetch<T = Dict>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof (data as Dict)?.detail === "string" ? (data as Dict).detail : `Request failed (${res.status})`);
  }
  return data as T;
}

/* --------------------------------------------------------------- shortlist */

export function readLocalShortlist(): Dict[] {
  if (typeof window === "undefined") return [];
  try { return JSON.parse(window.localStorage.getItem(SHORTLIST_KEY) ?? "[]"); } catch { return []; }
}

/** Map the browser-local shortlist shape onto the server's payload. */
function toServerItems(items: Dict[]) {
  return items
    .filter((i) => i?.slug)
    .map((i) => ({
      entity_type: (i.type === "university" || i.type === "scholarship" ? i.type : "program") as string,
      slug: String(i.slug),
      label: i.name ?? null,
      meta: {
        href: i.href ?? null, university: i.meta ?? null,
        tuition: i.tuition ?? null, currency: i.currency ?? null, level: i.level ?? null,
      },
    }));
}

/**
 * Push whatever is in localStorage up to the student's server-side shortlist.
 * Safe to call repeatedly — the server skips duplicates.
 */
export async function syncShortlist(token?: string | null): Promise<number> {
  const t = token ?? getProfileToken();
  if (!t) return 0;
  const items = toServerItems(readLocalShortlist());
  if (!items.length) return 0;
  try {
    const res = await portalFetch<{ added: number }>(`/portal/${t}/shortlist/sync`, {
      method: "POST", body: JSON.stringify(items),
    });
    return res.added ?? 0;
  } catch {
    return 0;
  }
}

export const DOC_LABELS: Record<string, string> = {
  PASSPORT: "Passport",
  SSC_CERTIFICATE: "SSC certificate",
  SSC_TRANSCRIPT: "SSC transcript",
  HSC_CERTIFICATE: "HSC certificate",
  HSC_TRANSCRIPT: "HSC transcript",
  BACHELOR_CERTIFICATE: "Bachelor certificate",
  BACHELOR_TRANSCRIPT: "Bachelor transcript",
  MASTER_CERTIFICATE: "Master certificate",
  MASTER_TRANSCRIPT: "Master transcript",
  IELTS_TRMC: "IELTS TRF",
  TOEFL_REPORT: "TOEFL report",
  PTE_REPORT: "PTE report",
  MOI_CERTIFICATE: "Medium of instruction certificate",
  GRE_REPORT: "GRE report",
  GMAT_REPORT: "GMAT report",
  CV: "CV / résumé",
  SOP: "Statement of purpose",
  RECOMMENDATION_LETTER: "Recommendation letter",
  EXPERIENCE_LETTER: "Experience letter",
  BANK_STATEMENT: "Bank statement",
  AFFIDAVIT_OF_SUPPORT: "Affidavit of support",
  RESEARCH_PROPOSAL: "Research proposal",
  PUBLICATION: "Publication",
  PHOTO: "Passport photo",
  OTHER: "Other document",
};

export const STATUS_LABELS: Record<string, string> = {
  PLANNING: "Planning",
  ELIGIBILITY_CHECKED: "Eligibility checked",
  DOCUMENTS_PENDING: "Documents pending",
  APPLICATION_PREPARATION: "Preparing application",
  SUBMITTED: "Submitted",
  UNDER_REVIEW: "University review",
  CONDITIONAL_OFFER: "Conditional offer",
  UNCONDITIONAL_OFFER: "Unconditional offer",
  DEPOSIT: "Deposit paid",
  COE_I20_CAS: "CoE / I-20 / CAS",
  VISA_PREPARATION: "Visa preparation",
  VISA_SUBMITTED: "Visa submitted",
  VISA_GRANTED: "Visa granted",
  COMPLETED: "Completed",
  WITHDRAWN: "Withdrawn",
  REJECTED: "Not successful",
};

export function statusTone(status: string): string {
  if (["VISA_GRANTED", "COMPLETED", "UNCONDITIONAL_OFFER"].includes(status)) return "bg-emerald-100 text-emerald-800 ring-emerald-200";
  if (["REJECTED", "WITHDRAWN"].includes(status)) return "bg-rose-100 text-rose-800 ring-rose-200";
  if (["SUBMITTED", "UNDER_REVIEW", "VISA_SUBMITTED", "CONDITIONAL_OFFER"].includes(status)) return "bg-amber-100 text-amber-900 ring-amber-200";
  return "bg-slate-100 text-slate-700 ring-slate-200";
}
