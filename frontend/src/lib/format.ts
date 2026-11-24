export function money(value?: number | null, currency = "USD", short = false): string {
  if (value === null || value === undefined) return "Not published";
  if (short && value >= 1000) return `${currency} ${Math.round(value / 1000)}k`;
  return `${currency} ${Math.round(value).toLocaleString("en-US")}`;
}

export function range(min?: number | null, max?: number | null, currency = "USD"): string {
  if (!min && !max) return "Not published";
  if (min && max) return `${currency} ${min.toLocaleString()} – ${max.toLocaleString()}`;
  return money(min ?? max, currency);
}

export function months(n?: number | null): string {
  if (!n) return "Varies";
  if (n % 12 === 0) return `${n / 12} year${n / 12 > 1 ? "s" : ""}`;
  return `${n} months`;
}

export function dateLabel(value?: string | null): string {
  if (!value) return "TBC";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function dateTimeLabel(value?: string | null): string {
  if (!value) return "TBC";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export const LEVEL_LABEL: Record<string, string> = {
  BACHELOR: "Bachelor", MASTER: "Master", PHD: "PhD",
  DIPLOMA: "Diploma", FOUNDATION: "Foundation", CERTIFICATE: "Certificate",
};

export const CONFIDENCE_STYLE: Record<string, { label: string; cls: string }> = {
  VERIFIED: { label: "Verified", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  NEEDS_REVIEW: { label: "Needs review", cls: "bg-amber-50 text-amber-700 ring-amber-200" },
  OUTDATED: { label: "Outdated", cls: "bg-orange-50 text-orange-700 ring-orange-200" },
  CONFLICTING: { label: "Conflicting sources", cls: "bg-rose-50 text-rose-700 ring-rose-200" },
  UNVERIFIED: { label: "Unverified", cls: "bg-slate-100 text-slate-600 ring-slate-200" },
};

export const VERDICT_STYLE: Record<string, { label: string; cls: string; dot: string }> = {
  MEETS: { label: "Meets", cls: "bg-emerald-50 text-emerald-800 ring-emerald-200", dot: "bg-emerald-500" },
  PARTIALLY_MEETS: { label: "Partially meets", cls: "bg-amber-50 text-amber-800 ring-amber-200", dot: "bg-amber-500" },
  DOES_NOT_MEET: { label: "Does not meet", cls: "bg-rose-50 text-rose-800 ring-rose-200", dot: "bg-rose-500" },
  REQUIRES_REVIEW: { label: "Requires review", cls: "bg-sky-50 text-sky-800 ring-sky-200", dot: "bg-sky-500" },
  INFORMATION_UNAVAILABLE: { label: "Information unavailable", cls: "bg-slate-100 text-slate-700 ring-slate-200", dot: "bg-slate-400" },
};

export const EMAIL_STATUS_LABEL: Record<string, string> = {
  OFFICIAL_UNIVERSITY_EMAIL: "Official university email",
  PUBLIC_PROFESSIONAL_EMAIL: "Public professional email",
  UNAVAILABLE: "Not publicly available",
  NOT_PUBLISHED: "Not publicly available",
};

export function sourceTypeLabel(t?: string | null): string {
  if (!t) return "Source not recorded";
  return t
    .replace(/^TIER_\d+_/, "")
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());
}

export function universityHref(u: Dict): string {
  const country = u?.country?.slug ?? u?.country_slug ?? "world";
  return `/universities/${country}/${u.slug}`;
}

type Dict = Record<string, any>;
