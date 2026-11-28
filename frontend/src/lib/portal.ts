"use client";

/** Student-portal client helpers: token storage, API access and shortlist sync. */

import { invalidateAssistantSession } from "./assistant-session.ts";

type Dict = Record<string, any>;

const TOKEN_KEY = "scholara.profile.token";
export const PROFILE_STORAGE_KEY = TOKEN_KEY;
export const IDENTITY_REVISION_KEY = "scholara.profile.revision";
const NAME_KEY = "scholara.profile.name";
const LEGACY_SHORTLIST_KEY = "scholara.shortlist";
const SHORTLIST_PREFIX = "scholara.shortlist.v2.";
const ANONYMOUS_ID = "anonymous";
const SHORTLIST_CONTEXT = "scholara.shortlist.identity.v2:";

// Cross-tab profile changes are visible through the storage event; a custom event alone is
// invisible to other tabs. Revoke this tab's old assistant session before it is reused. The
// legacy unscoped shortlist cannot be attributed to a student, so it is discarded rather than
// migrated into whichever profile happens to open next.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key === TOKEN_KEY) void invalidateAssistantSession();
  });
  try { window.localStorage.removeItem(LEGACY_SHORTLIST_KEY); } catch { /* storage unavailable */ }
}

export function getProfileToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

/** Monotonic browser-local identity revision, used to invalidate pending portal entry work. */
export function getIdentityRevision(): number {
  if (typeof window === "undefined") return 0;
  const value = Number(window.localStorage.getItem(IDENTITY_REVISION_KEY) ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function bumpIdentityRevision() {
  try {
    window.localStorage.setItem(IDENTITY_REVISION_KEY, String(getIdentityRevision() + 1));
  } catch { /* storage unavailable; response-time token checks still apply */ }
}

export function getProfileName(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(NAME_KEY);
}

export function setProfileToken(token: string, name?: string) {
  if (typeof window === "undefined") return;
  const replaced = window.localStorage.getItem(TOKEN_KEY) !== token;
  window.localStorage.setItem(TOKEN_KEY, token);
  if (name) window.localStorage.setItem(NAME_KEY, name);
  // Ownership transition: never let the previous student's assistant session or a pending
  // token-entry request survive it.
  if (replaced) {
    bumpIdentityRevision();
    void invalidateAssistantSession();
  }
  window.dispatchEvent(new Event("scholara:profile"));
}

export function clearProfileToken() {
  if (typeof window === "undefined") return;
  const signingOut = window.localStorage.getItem(TOKEN_KEY);
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(NAME_KEY);
  bumpIdentityRevision();
  if (signingOut) void clearLocalShortlist(signingOut);
  // Anonymous items may be the same person's pre-login shortlist; do not leave them for the
  // next anonymous visitor on a shared device.
  void clearLocalShortlist(null);
  void invalidateAssistantSession();
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

/**
 * A non-reversible, domain-separated bucket name for one identity. The raw portal token is
 * never used as a storage key or written into shortlist metadata.
 */
export async function shortlistBucket(token: string | null): Promise<string | null> {
  try {
    const subtle = globalThis.crypto?.subtle;
    if (!subtle) return null;
    const payload = new TextEncoder().encode(SHORTLIST_CONTEXT + (token ?? ANONYMOUS_ID));
    const digest = await subtle.digest("SHA-256", payload);
    const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
    return SHORTLIST_PREFIX + hex;
  } catch {
    return null;
  }
}

/** Read only the active identity's bucket. Legacy global data is never considered. */
export async function readLocalShortlist(token?: string | null): Promise<Dict[]> {
  if (typeof window === "undefined") return [];
  const identity = token === undefined ? getProfileToken() : token;
  const key = await shortlistBucket(identity);
  if (!key) return [];
  if (token === undefined && getProfileToken() !== identity) return [];
  try { return JSON.parse(window.localStorage.getItem(key) ?? "[]"); } catch { return []; }
}

export async function writeLocalShortlist(items: Dict[], token?: string | null): Promise<boolean> {
  if (typeof window === "undefined") return false;
  const identity = token === undefined ? getProfileToken() : token;
  if (token === undefined && getProfileToken() !== identity) return false;
  const key = await shortlistBucket(identity);
  if (!key) return false;
  if (token === undefined && getProfileToken() !== identity) return false;
  try { window.localStorage.setItem(key, JSON.stringify(items)); return true; } catch { return false; }
}

export async function clearLocalShortlist(token?: string | null): Promise<void> {
  if (typeof window === "undefined") return;
  const identity = token === undefined ? getProfileToken() : token;
  const key = await shortlistBucket(identity);
  if (!key) return;
  try { window.localStorage.removeItem(key); } catch { /* storage unavailable */ }
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
  if (!t || getProfileToken() !== t) return 0;
  const items = toServerItems(await readLocalShortlist(t));
  if (!items.length || getProfileToken() !== t) return 0;
  try {
    const res = await portalFetch<{ added: number }>(`/portal/${t}/shortlist/sync`, {
      method: "POST", body: JSON.stringify(items),
    });
    // A response for a superseded identity must not be applied by any caller.
    return getProfileToken() === t ? (res.added ?? 0) : 0;
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
