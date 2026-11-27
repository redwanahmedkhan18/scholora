import Link from "next/link";
import {
  CONFIDENCE_STYLE, LEVEL_LABEL, VERDICT_STYLE, dateLabel, money, months,
  sourceTypeLabel, universityHref,
} from "@/lib/format";
import { Badge, Card } from "@/components/ui/primitives";

/**
 * Domain components (university, programme, scholarship, faculty cards and the provenance
 * block). These sit on top of the shadcn/ui primitives in `ui/primitives.tsx` rather than
 * re-declaring their own borders and chips, so a change to Card or Badge reaches every surface.
 */

type Dict = Record<string, any>;

/* ------------------------------------------------------------------ layout */
export function Section({
  title, subtitle, children, href, hrefLabel, tone = "light", id,
}: {
  title?: string; subtitle?: string | null; children: React.ReactNode;
  href?: string; hrefLabel?: string; tone?: "light" | "dark" | "soft"; id?: string;
}) {
  const bg = tone === "dark" ? "bg-brand-950 text-white" : tone === "soft" ? "bg-white" : "";
  return (
    <section id={id} className={`${bg} py-12 sm:py-16`}>
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        {(title || href) && (
          <div className="mb-7 flex flex-wrap items-end justify-between gap-3">
            <div className="max-w-2xl">
              {title && (
                <h2 className={`text-2xl font-bold tracking-tight sm:text-3xl ${tone === "dark" ? "text-white" : "text-slate-900"}`}>
                  {title}
                </h2>
              )}
              {subtitle && (
                <p className={`mt-2 text-sm sm:text-base ${tone === "dark" ? "text-brand-100" : "text-slate-600"}`}>
                  {subtitle}
                </p>
              )}
            </div>
            {href && (
              <Link href={href} className={`shrink-0 text-sm font-semibold ${tone === "dark" ? "text-accent-400 hover:text-accent-500" : "text-brand-800 hover:text-brand-600"}`}>
                {hrefLabel ?? "View all"} →
              </Link>
            )}
          </div>
        )}
        {children}
      </div>
    </section>
  );
}

export function PageHeader({
  eyebrow, title, description, children,
}: { eyebrow?: string; title: string; description?: string | null; children?: React.ReactNode }) {
  return (
    <header className="border-b border-slate-200 bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 sm:py-14">
        {eyebrow && <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent-400">{eyebrow}</p>}
        <h1 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">{title}</h1>
        {description && <p className="mt-3 max-w-3xl text-sm leading-relaxed text-brand-100 sm:text-base">{description}</p>}
        {children && <div className="mt-6">{children}</div>}
      </div>
    </header>
  );
}

export function Breadcrumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mx-auto max-w-7xl px-4 pt-5 sm:px-6">
      <ol className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
        {items.map((it, i) => (
          <li key={i} className="flex items-center gap-1.5">
            {it.href ? <Link href={it.href} className="hover:text-brand-800">{it.label}</Link> : <span className="text-slate-700">{it.label}</span>}
            {i < items.length - 1 && <span aria-hidden>/</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <Card className="p-10 text-center">
      <p className="text-base font-semibold text-slate-800">{title}</p>
      {hint && <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">{hint}</p>}
    </Card>
  );
}

/* ------------------------------------------------------------------ badges */
const CONFIDENCE_VARIANT = {
  VERIFIED: "verified",
  NEEDS_REVIEW: "review",
  OUTDATED: "outdated",
  CONFLICTING: "conflicting",
  UNVERIFIED: "unverified",
} as const;

export function ConfidenceBadge({ confidence }: { confidence?: string | null }) {
  const key = (confidence ?? "UNVERIFIED") as keyof typeof CONFIDENCE_VARIANT;
  const style = CONFIDENCE_STYLE[key] ?? CONFIDENCE_STYLE.UNVERIFIED;
  return <Badge variant={CONFIDENCE_VARIANT[key] ?? "unverified"}>{style.label}</Badge>;
}

export function VerdictBadge({ status }: { status: string }) {
  const s = VERDICT_STYLE[status] ?? VERDICT_STYLE.INFORMATION_UNAVAILABLE;
  return (
    <span className={`chip ring-1 ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden /> {s.label}
    </span>
  );
}

/** Mandatory provenance block - every important field must show where it came from. */
export function SourceNote({ provenance, label = "Source" }: { provenance?: Dict | null; label?: string }) {
  if (!provenance) return null;
  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-600">
      <div className="flex flex-wrap items-center gap-2">
        <ConfidenceBadge confidence={provenance.confidence} />
        <span className="font-semibold text-slate-700">{label}:</span>
        {provenance.source_url ? (
          <a href={provenance.source_url} target="_blank" rel="noopener noreferrer nofollow" className="break-all text-brand-700 underline underline-offset-2">
            {sourceTypeLabel(provenance.source_type)}
          </a>
        ) : (
          <span>{sourceTypeLabel(provenance.source_type)}</span>
        )}
      </div>
      <p className="mt-1">
        Verified {dateLabel(provenance.verified_at)}
        {provenance.review_due_at ? ` · next review ${dateLabel(provenance.review_due_at)}` : ""}
      </p>
    </div>
  );
}

export function Disclaimer({ children }: { children?: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] leading-relaxed text-amber-900">
      {children ?? (
        <>
          Figures and requirements come from the cited source on the date shown. Universities change requirements
          without notice. Always confirm with the official source or your counselor before paying any fee. Nothing here
          is an admission, scholarship or visa guarantee.
        </>
      )}
    </p>
  );
}

/* ------------------------------------------------------------------ cards */
export function UniversityCard({ u }: { u: Dict }) {
  const rank = u.rankings?.[0];
  return (
    <Link href={universityHref(u)} className="card flex h-full flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            {u.country?.flag_emoji} {u.city}
          </p>
          <h3 className="mt-1 text-base font-bold leading-snug text-slate-900">{u.name}</h3>
        </div>
        {u.is_featured && <span className="chip bg-accent-500/10 text-accent-600 ring-1 ring-accent-500/30">Featured</span>}
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {u.institution_type && <span className="chip bg-slate-100 text-slate-700">{String(u.institution_type).toLowerCase()}</span>}
        {u.established_year && <span className="chip bg-slate-100 text-slate-700">Est. {u.established_year}</span>}
        {typeof u.program_count === "number" && <span className="chip bg-brand-50 text-brand-800">{u.program_count} programmes</span>}
      </div>
      {rank && (
        <p className="mt-3 text-xs text-slate-600">
          <span className="font-semibold text-slate-800">{rank.rank_overall}</span> · {rank.provider} {rank.edition_year}
        </p>
      )}
      <span className="mt-auto pt-4 text-sm font-semibold text-brand-800">View university →</span>
    </Link>
  );
}

export function ProgramCard({ p, compact = false }: { p: Dict; compact?: boolean }) {
  const req = p.requirement_summary ?? {};
  return (
    <Link href={`/programs/${p.slug}`} className="card flex h-full flex-col p-5">
      <div className="flex items-center justify-between gap-2">
        <span className="chip bg-brand-800 text-white">{LEVEL_LABEL[p.degree_level] ?? p.degree_level}</span>
        {p.scholarship_available && <span className="chip bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">Funding listed</span>}
      </div>
      <h3 className="mt-3 text-base font-bold leading-snug text-slate-900">{p.name}</h3>
      <p className="mt-1 text-xs text-slate-600">
        {p.university?.country?.flag_emoji} {p.university?.name} · {p.campus ?? p.university?.city}
      </p>
      {!compact && (
        <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
          <div><dt className="text-slate-500">Tuition / year</dt><dd className="font-semibold text-slate-900">{money(p.tuition_annual, p.currency)}</dd></div>
          <div><dt className="text-slate-500">Duration</dt><dd className="font-semibold text-slate-900">{months(p.duration_months)}</dd></div>
          <div><dt className="text-slate-500">Min CGPA</dt><dd className="font-semibold text-slate-900">{req.min_cgpa ?? "Not published"}</dd></div>
          <div><dt className="text-slate-500">IELTS</dt><dd className="font-semibold text-slate-900">{req.ielts_overall ?? "Not published"}</dd></div>
        </dl>
      )}
      {p.intakes?.length > 0 && (
        <p className="mt-3 text-[11px] text-slate-500">
          Intakes: {p.intakes.slice(0, 3).map((i: Dict) => i.label).join(" · ")}
        </p>
      )}
      <span className="mt-auto pt-4 text-sm font-semibold text-brand-800">Requirements & fees →</span>
    </Link>
  );
}

export function ScholarshipCard({ s }: { s: Dict }) {
  const value =
    s.amount_type === "FULL" ? "Full funding"
    : s.amount_type === "PERCENTAGE" ? `${s.percentage}% tuition`
    : s.amount_value ? money(s.amount_value, s.currency)
    : "Value varies";
  return (
    <Link href={`/scholarships/${s.slug}`} className="card flex h-full flex-col p-5">
      <div className="flex items-start justify-between gap-2">
        <span className="chip bg-accent-500/15 text-accent-600 ring-1 ring-accent-500/30">{value}</span>
        {s.country?.flag_emoji && <span className="text-lg" aria-hidden>{s.country.flag_emoji}</span>}
      </div>
      <h3 className="mt-3 text-base font-bold leading-snug text-slate-900">{s.name}</h3>
      <p className="mt-1 text-xs text-slate-600">{s.provider}</p>
      <p className="mt-3 line-clamp-3 text-xs leading-relaxed text-slate-600">{s.eligibility_summary}</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {(s.degree_levels ?? []).map((l: string) => (
          <span key={l} className="chip bg-slate-100 text-slate-700">{LEVEL_LABEL[l] ?? l}</span>
        ))}
      </div>
      {s.deadline && <p className="mt-3 text-[11px] text-slate-500">Stated deadline: {dateLabel(s.deadline)}</p>}
      <span className="mt-auto pt-4 text-sm font-semibold text-brand-800">Eligibility criteria →</span>
    </Link>
  );
}

export function StoryCard({ s }: { s: Dict }) {
  return (
    <Link href={`/success-stories/${s.slug}`} className="card flex h-full flex-col p-5">
      <div className="flex items-center gap-3">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-brand-800 text-sm font-bold text-white">
          {String(s.student_name ?? "S").split(" ").map((w: string) => w[0]).slice(0, 2).join("")}
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-bold text-slate-900">{s.student_name}</p>
          <p className="truncate text-[11px] text-slate-500">{s.country?.flag_emoji} {s.country?.name} · {s.year}</p>
        </div>
      </div>
      <p className="mt-3 text-sm font-semibold leading-snug text-slate-900">{s.program_name}</p>
      <p className="text-xs text-slate-600">{s.university_name}</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {s.scholarship && <span className="chip bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">{s.scholarship}</span>}
        {s.visa_result && <span className="chip bg-slate-100 text-slate-700">{s.visa_result}</span>}
      </div>
      <span className="mt-auto pt-4 text-sm font-semibold text-brand-800">Read the journey →</span>
    </Link>
  );
}

export function FacultyCard({ f }: { f: Dict }) {
  return (
    <Link href={`/faculty/${f.slug}`} className="card flex h-full flex-col p-5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-bold leading-snug text-slate-900">{f.full_name}</h3>
          <p className="mt-0.5 text-xs text-slate-600">{f.title}</p>
        </div>
        {f.accepting_phd === "YES" && <span className="chip bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">Accepting PhD</span>}
      </div>
      <p className="mt-2 text-xs text-slate-600">
        {f.university?.country?.flag_emoji} {f.university?.name}
        {f.department?.name ? ` · ${f.department.name}` : ""}
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {(f.research_interests ?? []).slice(0, 4).map((r: string) => (
          <span key={r} className="chip bg-brand-50 text-brand-800">{r}</span>
        ))}
      </div>
      <span className="mt-auto pt-4 text-sm font-semibold text-brand-800">Research profile →</span>
    </Link>
  );
}

export function ServiceCard({ s }: { s: Dict }) {
  return (
    <Link href={`/services/${s.slug}`} className="card flex h-full flex-col p-5">
      <span className="text-2xl" aria-hidden>{s.icon ?? "📘"}</span>
      <h3 className="mt-3 text-base font-bold leading-snug text-slate-900">{s.title}</h3>
      <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-slate-600">{s.summary}</p>
      <span className="mt-auto pt-4 text-sm font-semibold text-brand-800">What is included →</span>
    </Link>
  );
}

export function CountryCard({ c }: { c: Dict }) {
  const soon = c.status === "COMING_SOON";
  const inner = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="text-3xl" aria-hidden>{c.flag_emoji}</span>
        {soon
          ? <span className="chip bg-slate-100 text-slate-600">Coming soon</span>
          : <span className="chip bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">Open</span>}
      </div>
      <h3 className="mt-3 text-base font-bold text-slate-900">{c.name}</h3>
      <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-600">{c.tagline}</p>
      {c.counts && (
        <p className="mt-3 text-[11px] text-slate-500">
          {c.counts.universities} universities · {c.counts.programs} programmes · {c.counts.scholarships} scholarships
        </p>
      )}
      <span className={`mt-auto pt-4 text-sm font-semibold ${soon ? "text-slate-400" : "text-brand-800"}`}>
        {soon ? "Register interest" : "Explore destination →"}
      </span>
    </>
  );
  return soon
    ? <div className="card flex h-full flex-col p-5 opacity-80">{inner}</div>
    : <Link href={`/study-destinations/${c.slug}`} className="card flex h-full flex-col p-5">{inner}</Link>;
}

export function EventCard({ e }: { e: Dict }) {
  return (
    <div className="card flex h-full flex-col p-5">
      <div className="flex items-center justify-between gap-2">
        <span className="chip bg-brand-800 text-white">{e.mode}</span>
        <span className="text-[11px] font-semibold text-slate-500">{dateLabel(e.start_at)}</span>
      </div>
      <h3 className="mt-3 text-base font-bold leading-snug text-slate-900">{e.title}</h3>
      <p className="mt-1 text-xs text-slate-600">{e.venue}</p>
      {e.description && <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-slate-600">{e.description}</p>}
      <Link href={`/events#${e.slug}`} className="mt-auto pt-4 text-sm font-semibold text-brand-800">Register free →</Link>
    </div>
  );
}

export function PostCard({ p }: { p: Dict }) {
  return (
    <Link href={`/blog/${p.slug}`} className="card flex h-full flex-col p-5">
      <span className="chip w-fit bg-slate-100 text-slate-700">{p.category}</span>
      <h3 className="mt-3 text-base font-bold leading-snug text-slate-900">{p.title}</h3>
      <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-slate-600">{p.excerpt}</p>
      <p className="mt-auto pt-4 text-[11px] text-slate-500">{dateLabel(p.published_at)} · {p.reading_minutes} min read</p>
    </Link>
  );
}

export function Grid({ children, cols = 3 }: { children: React.ReactNode; cols?: 2 | 3 | 4 }) {
  const cls = cols === 4
    ? "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4"
    : cols === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3";
  return <div className={`grid gap-4 ${cls}`}>{children}</div>;
}

export function Stat({ value, label }: { value: React.ReactNode; label: string }) {
  return (
    <div className="rounded-xl border border-white/15 bg-white/5 px-4 py-3 text-center backdrop-blur">
      <p className="text-xl font-bold text-white sm:text-2xl">{value}</p>
      <p className="mt-0.5 text-[11px] uppercase tracking-wide text-brand-100">{label}</p>
    </div>
  );
}
