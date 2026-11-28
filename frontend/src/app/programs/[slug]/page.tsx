import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { LEVEL_LABEL, dateLabel, money, months, universityHref } from "@/lib/format";
import { ShortlistButton, StartApplicationButton } from "@/components/site-chrome";
import { Disclaimer, FacultyCard, Grid, ProgramCard, ScholarshipCard, Section, SourceNote } from "@/components/ui";
import { jsonLdScript } from "@/lib/json-ld";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.programs ?? []).slice(0, 200).map((p: Dict) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/programs/${slug}`, 300);
  if (!data) return { title: "Programme not found" };
  const p = data.program;
  return {
    title: p.seo?.title ?? `${p.name} at ${p.university?.name}`,
    description: p.seo?.description,
    alternates: { canonical: `/programs/${p.slug}` },
    openGraph: { title: p.name, description: p.seo?.description, type: "article" },
  };
}

export default async function ProgramPage({ params }: Props) {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/programs/${slug}`, 300);
  if (!data) notFound();
  const p = data.program;
  const r = p.requirements ?? {};
  const cost = data.cost_estimate ?? {};

  const ld = {
    "@context": "https://schema.org",
    "@type": "Course",
    name: p.name,
    description: p.overview,
    provider: { "@type": "CollegeOrUniversity", name: p.university?.name, sameAs: p.university?.website },
    educationalCredentialAwarded: p.award,
    timeRequired: p.duration_months ? `P${p.duration_months}M` : undefined,
    inLanguage: p.language,
    offers: p.tuition_annual
      ? { "@type": "Offer", price: p.tuition_annual, priceCurrency: p.currency, category: "Annual tuition" }
      : undefined,
    hasCourseInstance: (p.intakes ?? []).map((i: Dict) => ({
      "@type": "CourseInstance", courseMode: p.study_mode, startDate: i.class_start, name: i.label,
    })),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(ld) }} />

      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/programs" className="hover:text-white">Programmes</Link> /{" "}
            <Link href={universityHref(p.university)} className="hover:text-white">{p.university?.name}</Link> /{" "}
            <span className="text-white">{p.name}</span>
          </nav>

          <div className="mt-4 flex flex-wrap items-start justify-between gap-5">
            <div className="max-w-3xl">
              <div className="flex flex-wrap gap-2">
                <span className="chip bg-accent-500 text-brand-950">{LEVEL_LABEL[p.degree_level] ?? p.degree_level}</span>
                {p.award && <span className="chip bg-white/10 text-white ring-1 ring-white/20">{p.award}</span>}
                {p.discipline && <span className="chip bg-white/10 text-white ring-1 ring-white/20">{p.discipline}</span>}
              </div>
              <h1 className="mt-3 text-3xl font-bold tracking-tight sm:text-4xl">{p.name}</h1>
              <p className="mt-2 text-sm font-medium text-accent-400">
                {p.university?.country?.flag_emoji}{" "}
                <Link href={universityHref(p.university)} className="underline underline-offset-2">{p.university?.name}</Link>
                {" · "}{p.campus ?? p.university?.city}
              </p>
              <p className="mt-3 text-sm leading-relaxed text-brand-100">{p.overview}</p>
            </div>

            <div className="w-full max-w-xs space-y-2">
              <Link href={`/profile-assessment?program=${p.slug}`}
                className="block rounded-xl bg-accent-500 px-4 py-3 text-center text-sm font-bold text-brand-950 hover:bg-accent-400">
                Check my eligibility
              </Link>
              <ShortlistButton item={{ type: "program", slug: p.slug, name: p.name, href: `/programs/${p.slug}`, meta: p.university?.name, tuition: p.tuition_annual, currency: p.currency, level: p.degree_level }} className="w-full" />
              <StartApplicationButton programSlug={p.slug} intakes={(p.intakes ?? []).map((i: any) => i.label)} />
              <Link href="/appointments" className="block rounded-xl bg-white/10 px-4 py-3 text-center text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
                Discuss with a counselor
              </Link>
            </div>
          </div>

          <dl className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Tuition / year", money(p.tuition_annual, p.currency)],
              ["Duration", months(p.duration_months)],
              ["Min CGPA", r.academic?.min_cgpa ? `${r.academic.min_cgpa}/${r.academic.cgpa_scale ?? 4}` : "Not published"],
              ["IELTS", r.english?.ielts_overall ?? "Not published"],
            ].map(([k, v]) => (
              <div key={k as string} className="rounded-2xl border border-white/15 bg-white/5 p-4 backdrop-blur">
                <dt className="text-[11px] uppercase tracking-wide text-brand-100">{k}</dt>
                <dd className="mt-0.5 text-base font-bold text-white">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
          <div className="space-y-6">
            {/* ------------------------------------------------ requirements */}
            <div className="card p-6">
              <h2 className="text-lg font-bold text-slate-900">Entry requirements</h2>
              <p className="mt-1 text-xs text-slate-500">
                Stored for this programme specifically. Other programmes at {p.university?.name} may differ.
              </p>

              <h3 className="mt-5 text-sm font-bold uppercase tracking-wide text-slate-600">Academic</h3>
              <dl className="mt-2 divide-y divide-slate-100 text-sm">
                {[
                  ["Minimum qualification", r.academic?.min_qualification],
                  ["Minimum CGPA", r.academic?.min_cgpa ? `${r.academic.min_cgpa} on a ${r.academic.cgpa_scale ?? 4} scale` : null],
                  ["Relevant majors", (r.academic?.relevant_majors ?? []).join(", ") || null],
                  ["Required subjects", (r.academic?.required_subjects ?? []).join(", ") || null],
                  ["Prerequisite courses", (r.academic?.prerequisites ?? []).join(", ") || null],
                  ["Degree equivalency", r.academic?.degree_equivalency],
                  ["Study gap policy", r.academic?.max_study_gap_years ? `${r.academic.study_gap_policy} (up to ${r.academic.max_study_gap_years} years)` : r.academic?.study_gap_policy],
                  ["Work experience", r.academic?.work_experience_years ? `${r.academic.work_experience_years} year(s)` : null],
                  ["Research experience", r.academic?.research_experience],
                ].filter(([, v]) => v).map(([k, v]) => (
                  <div key={k as string} className="flex flex-wrap gap-2 py-2.5">
                    <dt className="w-48 shrink-0 text-slate-500">{k}</dt>
                    <dd className="flex-1 font-medium text-slate-900">{String(v)}</dd>
                  </div>
                ))}
              </dl>

              <h3 className="mt-5 text-sm font-bold uppercase tracking-wide text-slate-600">English language</h3>
              <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["IELTS", r.english?.ielts_overall], ["Band minimum", r.english?.ielts_band_min],
                  ["TOEFL iBT", r.english?.toefl_ibt], ["PTE Academic", r.english?.pte_academic],
                  ["Duolingo", r.english?.duolingo],
                ].filter(([, v]) => v).map(([k, v]) => (
                  <div key={k as string} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-slate-500">{k}</p>
                    <p className="text-base font-bold text-slate-900">{String(v)}</p>
                  </div>
                ))}
              </div>
              <p className="mt-2 text-xs text-slate-600">
                MOI (medium of instruction letter) {r.english?.moi_accepted ? "is accepted for this programme" : "is not listed as accepted for this programme"}.
                {(r.english?.other_accepted_tests ?? []).length > 0 && ` Also accepted: ${r.english.other_accepted_tests.join(", ")}.`}
              </p>

              {(r.tests?.gre_required || r.tests?.gmat_required) && (
                <>
                  <h3 className="mt-5 text-sm font-bold uppercase tracking-wide text-slate-600">Standardised tests</h3>
                  <ul className="mt-2 space-y-1 text-sm text-slate-700">
                    {r.tests.gre_required && <li>GRE required — {r.tests.gre_score ?? "score not published"}</li>}
                    {r.tests.gmat_required && <li>GMAT required — {r.tests.gmat_score ?? "score not published"}</li>}
                  </ul>
                </>
              )}

              {(r.documents ?? []).length > 0 && (
                <>
                  <h3 className="mt-5 text-sm font-bold uppercase tracking-wide text-slate-600">Documents to prepare</h3>
                  <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
                    {r.documents.map((d: string) => (
                      <li key={d} className="flex gap-2 text-sm text-slate-700"><span className="text-brand-700" aria-hidden>▢</span>{d}</li>
                    ))}
                  </ul>
                </>
              )}

              {r.notes && <p className="mt-4 text-xs leading-relaxed text-slate-600">{r.notes}</p>}
              <SourceNote provenance={r.provenance} label="Requirements source" />
            </div>

            {/* ------------------------------------------------------ intakes */}
            {(p.intakes ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Intakes and deadlines</h2>
                <p className="mt-1 text-xs text-slate-500">Intakes are programme-specific, not university-wide.</p>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[36rem] text-left text-sm">
                    <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-3 py-2 font-semibold">Intake</th>
                        <th className="px-3 py-2 font-semibold">Status</th>
                        <th className="px-3 py-2 font-semibold">Applications open</th>
                        <th className="px-3 py-2 font-semibold">Deadline</th>
                        <th className="px-3 py-2 font-semibold">Classes start</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {p.intakes.map((i: Dict) => (
                        <tr key={i.id}>
                          <td className="px-3 py-2.5 font-semibold text-slate-900">{i.label}</td>
                          <td className="px-3 py-2.5">
                            <span className={`chip ring-1 ${i.status === "OPEN" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-600 ring-slate-200"}`}>
                              {i.status}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-slate-700">{dateLabel(i.application_opens)}</td>
                          <td className="px-3 py-2.5 font-semibold text-slate-900">{dateLabel(i.application_deadline)}</td>
                          <td className="px-3 py-2.5 text-slate-700">{dateLabel(i.class_start)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <SourceNote provenance={p.intakes[0]?.provenance} label="Intake source" />
              </div>
            )}

            {(p.specializations ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Specialisations</h2>
                <div className="mt-3 flex flex-wrap gap-2">
                  {p.specializations.map((s: string) => <span key={s} className="chip bg-brand-50 text-brand-800">{s}</span>)}
                </div>
              </div>
            )}

            {(p.application_procedure ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">How to apply</h2>
                <ol className="mt-3 space-y-2">
                  {p.application_procedure.map((step: string, i: number) => (
                    <li key={step} className="flex gap-3 text-sm text-slate-700">
                      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-800 text-[11px] font-bold text-white">{i + 1}</span>
                      <span className="pt-0.5">{step}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {p.career_outcomes && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">After graduation</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-700">{p.career_outcomes}</p>
              </div>
            )}
          </div>

          {/* -------------------------------------------------------- sidebar */}
          <aside className="space-y-6">
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Estimated annual cost</h2>
              <ul className="mt-3 divide-y divide-slate-100 text-sm">
                {(cost.breakdown ?? []).map((b: Dict) => (
                  <li key={b.label} className={`flex items-center justify-between gap-3 py-2 ${b.included_in_total ? "" : "text-slate-500"}`}>
                    <span>{b.label}</span>
                    <span className="font-semibold">{money(b.amount, cost.currency)}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-3 rounded-xl bg-brand-50 p-3">
                <p className="text-[11px] uppercase tracking-wide text-brand-800">Estimated annual total</p>
                <p className="text-2xl font-black text-brand-900">{money(cost.estimated_annual_total, cost.currency)}</p>
                {cost.one_time_costs ? (
                  <p className="mt-1 text-[11px] text-brand-800">Plus one-time costs of {money(cost.one_time_costs, cost.currency)}</p>
                ) : null}
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
                Living cost is a destination-level estimate and varies by city and lifestyle. Enter your own budget in
                the profile assessment to see how this programme compares against what you can actually fund.
              </p>
              <SourceNote provenance={p.provenance} label="Fee source" />
            </div>

            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Programme facts</h2>
              <dl className="mt-3 space-y-2.5 text-sm">
                {[
                  ["Award", p.award], ["Level", LEVEL_LABEL[p.degree_level] ?? p.degree_level],
                  ["Discipline", p.discipline], ["Duration", months(p.duration_months)],
                  ["Credits", p.credits], ["Study mode", p.study_mode], ["Language", p.language],
                  ["Department", p.department?.name],
                ].filter(([, v]) => v).map(([k, v]) => (
                  <div key={k as string} className="flex gap-3">
                    <dt className="w-28 shrink-0 text-slate-500">{k}</dt>
                    <dd className="font-medium text-slate-900">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <Disclaimer />
          </aside>
        </div>
      </div>

      {data.scholarships?.length > 0 && (
        <Section title="Funding you could apply for" tone="soft" subtitle="Listed because the published criteria cover this programme's level, university or destination. Always read the full criteria.">
          <Grid cols={3}>{data.scholarships.map((s: Dict) => <ScholarshipCard key={s.id} s={s} />)}</Grid>
        </Section>
      )}

      {data.faculty?.length > 0 && (
        <Section title="Supervisors in this department">
          <Grid cols={3}>{data.faculty.map((f: Dict) => <FacultyCard key={f.id} f={f} />)}</Grid>
        </Section>
      )}

      {data.related?.length > 0 && (
        <Section title="Related programmes" tone="soft" href="/programs">
          <Grid cols={4}>{data.related.map((x: Dict) => <ProgramCard key={x.id} p={x} compact />)}</Grid>
        </Section>
      )}
    </>
  );
}
