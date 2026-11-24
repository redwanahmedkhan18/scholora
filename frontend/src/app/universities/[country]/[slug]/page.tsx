import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { LEVEL_LABEL, dateLabel } from "@/lib/format";
import { ShortlistButton } from "@/components/site-chrome";
import {
  Breadcrumbs, Disclaimer, FacultyCard, Grid, ProgramCard, ScholarshipCard,
  Section, SourceNote, StoryCard,
} from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ country: string; slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.universities ?? []).map((u: Dict) => ({ country: u.country, slug: u.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { country, slug } = await params;
  const data = await apiOrNull<Dict>(`/universities/${slug}`, 300);
  if (!data) return { title: "University not found" };
  const u = data.university;
  return {
    title: u.seo?.title ?? u.name,
    description: u.seo?.description,
    alternates: { canonical: `/universities/${country}/${u.slug}` },
    openGraph: { title: u.name, description: u.seo?.description, type: "article" },
  };
}

export default async function UniversityPage({ params }: Props) {
  const { country, slug } = await params;
  const data = await apiOrNull<Dict>(`/universities/${slug}`, 300);
  if (!data) notFound();
  const u = data.university;

  const ld = {
    "@context": "https://schema.org",
    "@type": "CollegeOrUniversity",
    name: u.name,
    url: u.website,
    foundingDate: u.established_year ? String(u.established_year) : undefined,
    address: { "@type": "PostalAddress", addressLocality: u.city, addressCountry: u.country?.name },
    description: u.overview,
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld) }} />

      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/universities" className="hover:text-white">Universities</Link> /{" "}
            <Link href={`/study-destinations/${country}`} className="hover:text-white">{u.country?.name}</Link> /{" "}
            <span className="text-white">{u.name}</span>
          </nav>

          <div className="mt-4 flex flex-wrap items-start justify-between gap-5">
            <div className="max-w-3xl">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent-400">
                {u.country?.flag_emoji} {u.city} · {String(u.institution_type ?? "").toLowerCase()} institution
              </p>
              <h1 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">{u.name}</h1>
              <p className="mt-3 text-sm leading-relaxed text-brand-100">{u.overview}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {u.established_year && <span className="chip bg-white/10 text-white ring-1 ring-white/20">Established {u.established_year}</span>}
                {u.student_count && <span className="chip bg-white/10 text-white ring-1 ring-white/20">{u.student_count.toLocaleString()} students</span>}
                {u.international_students && <span className="chip bg-white/10 text-white ring-1 ring-white/20">{u.international_students.toLocaleString()} international</span>}
                <span className="chip bg-white/10 text-white ring-1 ring-white/20">{u.program_count} programmes recorded</span>
              </div>
            </div>

            <div className="w-full max-w-xs space-y-2">
              <Link href={`/programs?university=${u.slug}`} className="block rounded-xl bg-accent-500 px-4 py-3 text-center text-sm font-bold text-brand-950 hover:bg-accent-400">
                See all {u.program_count} programmes
              </Link>
              <ShortlistButton item={{ type: "university", slug: u.slug, name: u.name, href: `/universities/${country}/${u.slug}`, meta: u.city }} className="w-full" />
              <a href={u.website} target="_blank" rel="noopener noreferrer nofollow"
                className="block rounded-xl bg-white/10 px-4 py-3 text-center text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
                Official website ↗
              </a>
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
          <div className="space-y-6">
            {data.rankings?.length > 0 || u.rankings?.length > 0 ? (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Rankings</h2>
                <p className="mt-1 text-xs text-slate-500">
                  Rankings are stored per provider and per edition year. Different providers measure different things —
                  we never merge them into a single &ldquo;rank&rdquo;.
                </p>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[32rem] text-left text-sm">
                    <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-3 py-2 font-semibold">Provider</th>
                        <th className="px-3 py-2 font-semibold">Edition</th>
                        <th className="px-3 py-2 font-semibold">World</th>
                        <th className="px-3 py-2 font-semibold">National</th>
                        <th className="px-3 py-2 font-semibold">Source</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {(u.rankings ?? []).map((r: Dict, i: number) => (
                        <tr key={i}>
                          <td className="px-3 py-2 font-medium text-slate-900">{r.provider}</td>
                          <td className="px-3 py-2 text-slate-700">{r.edition_year}</td>
                          <td className="px-3 py-2 text-slate-700">{r.rank_overall ?? "—"}</td>
                          <td className="px-3 py-2 text-slate-700">{r.rank_national ?? "—"}</td>
                          <td className="px-3 py-2">
                            {r.provenance?.source_url
                              ? <a href={r.provenance.source_url} target="_blank" rel="noopener noreferrer nofollow" className="text-brand-700 underline underline-offset-2">View</a>
                              : <span className="text-slate-400">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}

            {Object.entries(data.programs_by_level ?? {}).map(([level, list]) => (
              <div key={level} className="card p-6">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-bold text-slate-900">{LEVEL_LABEL[level] ?? level} programmes</h2>
                  <Link href={`/programs?university=${u.slug}&degree_level=${level}`} className="text-xs font-semibold text-brand-800">View all →</Link>
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  {(list as Dict[]).slice(0, 4).map((p) => <ProgramCard key={p.id} p={p} compact />)}
                </div>
              </div>
            ))}

            {data.faculties?.length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Faculties and departments</h2>
                <p className="mt-1 text-xs text-slate-500">
                  {data.faculties.length} academic {data.faculties.length === 1 ? "division" : "divisions"},
                  {" "}{data.departments?.length ?? 0} departments.
                </p>
                {/* University › Faculty/School › Department › Programme. Each division is a
                    record with its own page, dean and source, not a label on a department. */}
                <div className="mt-4 space-y-4">
                  {data.faculties.map((f: Dict) => (
                    <div key={f.id} className="rounded-xl border border-slate-200 p-4">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div>
                          <Link href={`/faculties/${f.slug}`} className="text-sm font-bold text-brand-900 hover:underline">
                            {f.name}
                          </Link>
                          {f.dean_name && (
                            <p className="text-[11px] text-slate-500">{f.dean_title ?? "Dean"}: {f.dean_name}</p>
                          )}
                        </div>
                        <span className="chip bg-slate-100 text-[10px] text-slate-600">
                          {f.department_count} {f.department_count === 1 ? "department" : "departments"}
                        </span>
                      </div>
                      {f.departments?.length > 0 && (
                        <ul className="mt-3 divide-y divide-slate-100 border-t border-slate-100 pt-1">
                          {f.departments.map((d: Dict) => (
                            <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                              <p className="text-sm text-slate-800">{d.name}</p>
                              {d.website && (
                                <a href={d.website} target="_blank" rel="noopener noreferrer nofollow"
                                  className="text-xs font-semibold text-brand-800">Department site ↗</a>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {u.faqs?.length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Frequently asked</h2>
                <dl className="mt-3 divide-y divide-slate-100">
                  {u.faqs.map((f: Dict) => (
                    <div key={f.q} className="py-3">
                      <dt className="text-sm font-semibold text-slate-900">{f.q}</dt>
                      <dd className="mt-1 text-sm leading-relaxed text-slate-600">{f.a}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </div>

          <aside className="space-y-6">
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Institution record</h2>
              <dl className="mt-3 space-y-2.5 text-sm">
                {[
                  ["Country", `${u.country?.flag_emoji} ${u.country?.name}`],
                  ["City", u.city],
                  ["Campus", u.campus],
                  ["Type", String(u.institution_type ?? "").toLowerCase()],
                  ["Established", u.established_year],
                  ["Accreditation", u.accreditation],
                ].filter(([, v]) => v).map(([k, v]) => (
                  <div key={k as string} className="flex gap-3">
                    <dt className="w-32 shrink-0 text-slate-500">{k}</dt>
                    <dd className="font-medium text-slate-900">{String(v)}</dd>
                  </div>
                ))}
              </dl>
              <SourceNote provenance={u.provenance} label="Institution source" />
              <div className="mt-3 space-y-1.5 text-sm">
                {u.international_website && <p><a href={u.international_website} target="_blank" rel="noopener noreferrer nofollow" className="font-semibold text-brand-800">International office ↗</a></p>}
                {u.application_portal && <p><a href={u.application_portal} target="_blank" rel="noopener noreferrer nofollow" className="font-semibold text-brand-800">Application portal ↗</a></p>}
              </div>
            </div>

            {u.accommodation && (
              <div className="card p-6">
                <h2 className="text-base font-bold text-slate-900">Accommodation & campus life</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{u.accommodation}</p>
                {u.student_life && <p className="mt-2 text-sm leading-relaxed text-slate-600">{u.student_life}</p>}
              </div>
            )}

            <div className="card bg-brand-950 p-6 text-white">
              <h2 className="text-base font-bold">Apply through Scholara</h2>
              <p className="mt-2 text-sm leading-relaxed text-brand-100">
                We prepare the application, check every document against this university&apos;s stated requirements and
                track the deadline for you.
              </p>
              <div className="mt-4 space-y-2">
                <Link href="/profile-assessment" className="block rounded-xl bg-accent-500 px-4 py-2.5 text-center text-sm font-bold text-brand-950">Check my eligibility</Link>
                <Link href="/appointments" className="block rounded-xl bg-white/10 px-4 py-2.5 text-center text-sm font-bold text-white ring-1 ring-white/20">Book a counselor</Link>
              </div>
            </div>

            <Disclaimer />
          </aside>
        </div>
      </div>

      {data.scholarships?.length > 0 && (
        <Section title="Scholarships at this university" tone="soft" href={`/scholarships?university=${u.slug}`}>
          <Grid cols={3}>{data.scholarships.map((s: Dict) => <ScholarshipCard key={s.id} s={s} />)}</Grid>
        </Section>
      )}

      {data.professors?.length > 0 && (
        <Section title="Research supervisors" subtitle="Contact details are shown only where the university publishes them officially." href="/phd">
          <Grid cols={3}>{data.professors.map((f: Dict) => <FacultyCard key={f.id} f={f} />)}</Grid>
        </Section>
      )}

      {data.success_stories?.length > 0 && (
        <Section title="Students we placed here" tone="soft" href="/success-stories">
          <Grid cols={3}>{data.success_stories.map((s: Dict) => <StoryCard key={s.id} s={s} />)}</Grid>
        </Section>
      )}

      <div className="mx-auto max-w-7xl px-4 pb-10 text-xs text-slate-500 sm:px-6">
        Record last verified {dateLabel(u.provenance?.verified_at)}.
      </div>
    </>
  );
}
