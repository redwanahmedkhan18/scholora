import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { LEVEL_LABEL, dateLabel, money } from "@/lib/format";
import { ShortlistButton } from "@/components/site-chrome";
import { Disclaimer, SourceNote } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.scholarships ?? []).map((s: Dict) => ({ slug: s.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const s = await apiOrNull<Dict>(`/scholarships/${slug}`, 300);
  if (!s) return { title: "Scholarship not found" };
  return {
    title: `${s.name} — eligibility, value and deadline`,
    description: `${s.name} from ${s.provider}: published eligibility criteria, award value, deadline and official source for Bangladeshi students.`,
    alternates: { canonical: `/scholarships/${s.slug}` },
  };
}

export default async function ScholarshipPage({ params }: Props) {
  const { slug } = await params;
  const s = await apiOrNull<Dict>(`/scholarships/${slug}`, 300);
  if (!s) notFound();

  const value =
    s.amount_type === "FULL" ? "Full funding"
    : s.amount_type === "PERCENTAGE" ? `${s.percentage}% of tuition`
    : s.amount_value ? money(s.amount_value, s.currency)
    : "Value not published";

  return (
    <>
      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/scholarships" className="hover:text-white">Scholarships</Link> / <span className="text-white">{s.name}</span>
          </nav>
          <h1 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl">{s.name}</h1>
          <p className="mt-2 text-sm font-medium text-accent-400">
            {s.country?.flag_emoji} {s.provider}
            {s.university?.slug && (
              <> · <Link href={`/universities/${s.country?.slug}/${s.university.slug}`} className="underline underline-offset-2">{s.university.name}</Link></>
            )}
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <span className="chip bg-accent-500 text-brand-950">{value}</span>
            {s.covers_tuition && <span className="chip bg-white/10 text-white ring-1 ring-white/20">Covers tuition</span>}
            {s.covers_living && <span className="chip bg-white/10 text-white ring-1 ring-white/20">Covers living cost</span>}
            {(s.degree_levels ?? []).map((l: string) => (
              <span key={l} className="chip bg-white/10 text-white ring-1 ring-white/20">{LEVEL_LABEL[l] ?? l}</span>
            ))}
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
          <div className="space-y-6">
            <div className="card p-6">
              <h2 className="text-lg font-bold text-slate-900">Published eligibility criteria</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-700">{s.eligibility_summary}</p>
              <ul className="mt-4 space-y-2 text-sm text-slate-700">
                {s.min_cgpa && <li className="flex gap-2"><span className="text-brand-700" aria-hidden>▢</span>Minimum CGPA {s.min_cgpa} on a {s.cgpa_scale ?? 4} scale</li>}
                {s.min_ielts && <li className="flex gap-2"><span className="text-brand-700" aria-hidden>▢</span>IELTS {s.min_ielts} overall (or accepted equivalent)</li>}
                {(s.eligible_nationalities ?? []).length > 0 && (
                  <li className="flex gap-2"><span className="text-brand-700" aria-hidden>▢</span>Open to: {s.eligible_nationalities.join(", ")}</li>
                )}
                {(s.other_criteria ?? []).map((c: string) => (
                  <li key={c} className="flex gap-2"><span className="text-brand-700" aria-hidden>▢</span>{c}</li>
                ))}
              </ul>
              <p className="mt-4 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
                If your profile satisfies every line above, we will tell you that <strong>the stated eligibility
                criteria appear to be met</strong>. That is a statement about the published criteria, not a prediction
                of the outcome.
              </p>
              <SourceNote provenance={s.provenance} label="Scholarship source" />
            </div>

            {s.application_method && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">How to apply</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-700">{s.application_method}</p>
              </div>
            )}

            <Disclaimer />
          </div>

          <aside className="space-y-6">
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">At a glance</h2>
              <dl className="mt-3 space-y-2.5 text-sm">
                {[
                  ["Award", value],
                  ["Provider", s.provider],
                  ["Destination", `${s.country?.flag_emoji ?? ""} ${s.country?.name ?? "—"}`],
                  ["Levels", (s.degree_levels ?? []).map((l: string) => LEVEL_LABEL[l] ?? l).join(", ") || "Any"],
                  ["Stipend", s.stipend ?? "Not listed"],
                  ["Stated deadline", dateLabel(s.deadline)],
                ].map(([k, v]) => (
                  <div key={k as string} className="flex gap-3">
                    <dt className="w-32 shrink-0 text-slate-500">{k}</dt>
                    <dd className="font-medium text-slate-900">{String(v)}</dd>
                  </div>
                ))}
              </dl>
              <div className="mt-4 space-y-2">
                <ShortlistButton item={{ type: "scholarship", slug: s.slug, name: s.name, href: `/scholarships/${s.slug}`, meta: s.provider }} className="w-full" />
                {s.provenance?.source_url && (
                  <a href={s.provenance.source_url} target="_blank" rel="noopener noreferrer nofollow"
                    className="block rounded-xl bg-brand-800 px-4 py-2.5 text-center text-sm font-bold text-white">
                    Official scholarship page ↗
                  </a>
                )}
              </div>
            </div>

            <div className="card bg-brand-950 p-6 text-white">
              <h2 className="text-base font-bold">Check this against your profile</h2>
              <p className="mt-2 text-sm leading-relaxed text-brand-100">
                Add your CGPA, English score and budget once, and we will tell you which of the {" "}
                recorded scholarships you satisfy the stated criteria for.
              </p>
              <Link href="/profile-assessment" className="mt-4 block rounded-xl bg-accent-500 px-4 py-2.5 text-center text-sm font-bold text-brand-950">
                Run the free check
              </Link>
            </div>

            {s.country?.slug && (
              <Link href={`/scholarships?country=${s.country.slug}`} className="card block p-5 text-sm font-semibold text-brand-800">
                More {s.country.name} scholarships →
              </Link>
            )}
          </aside>
        </div>
      </div>
    </>
  );
}
