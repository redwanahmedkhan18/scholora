import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { LEVEL_LABEL, universityHref } from "@/lib/format";
import { Empty, FacultyCard, Grid, ProgramCard, Section, SourceNote } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

/**
 * An academic division: University › **Faculty/School** › Department › Programme.
 *
 * Note the deliberate split from `/faculty/[slug]`, which is a *professor*. The
 * specification uses "faculty" in both senses, so the person keeps the singular route and
 * the division takes the plural, exactly as the API does (`/faculty/{slug}` vs
 * `/faculties/{slug}`).
 */

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/faculties?page_size=100", 600);
  return (data?.items ?? []).map((f: Dict) => ({ slug: f.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/faculties/${slug}`, 300);
  if (!data) return { title: "Faculty not found" };
  const f = data.faculty;
  const u = data.university;
  const title = f.seo?.title ?? `${f.name} — ${u.name}`;
  const description =
    f.seo?.description ??
    `${f.name} at ${u.name}${u.country ? `, ${u.country}` : ""}. ` +
      `${f.department_count} departments and ${data.program_count} programmes, with entry requirements, ` +
      `fees and intakes recorded against their published sources.`;
  return {
    title,
    description,
    alternates: { canonical: `/faculties/${f.slug}` },
    openGraph: { title, description, type: "article" },
  };
}

export default async function FacultyPage({ params }: Props) {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/faculties/${slug}`, 300);
  if (!data) notFound();

  const f = data.faculty;
  const u = data.university;
  const levels: string[] = Object.keys(data.programs_by_level ?? {});

  const ld = {
    "@context": "https://schema.org",
    "@type": "CollegeOrUniversity",
    name: f.name,
    url: `/faculties/${f.slug}`,
    parentOrganization: { "@type": "CollegeOrUniversity", name: u.name },
    ...(f.website ? { sameAs: f.website } : {}),
    ...(f.overview ? { description: f.overview } : {}),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld) }} />

      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-4 py-8">
          <nav className="text-[11px] text-slate-500" aria-label="Breadcrumb">
            <Link href="/universities" className="hover:underline">Universities</Link>
            <span className="mx-1.5">/</span>
            <Link href={universityHref({ country_slug: u.country_slug, slug: u.slug })} className="hover:underline">
              {u.name}
            </Link>
            <span className="mx-1.5">/</span>
            <span className="text-slate-700">{f.name}</span>
          </nav>

          <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-black text-slate-900 sm:text-3xl">{f.name}</h1>
              <p className="mt-1 text-sm text-slate-600">
                {u.name}
                {u.country ? ` · ${u.country}` : ""}
              </p>
              {f.dean_name && (
                <p className="mt-1 text-xs text-slate-500">
                  {f.dean_title ?? "Dean"}: <span className="font-semibold text-slate-700">{f.dean_name}</span>
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="chip bg-slate-100 text-slate-700">
                {f.department_count} {f.department_count === 1 ? "department" : "departments"}
              </span>
              <span className="chip bg-brand-50 text-brand-900">
                {data.program_count} {data.program_count === 1 ? "programme" : "programmes"}
              </span>
              {f.website && (
                <a href={f.website} target="_blank" rel="noopener noreferrer nofollow"
                  className="chip bg-white text-brand-800 ring-1 ring-slate-200">
                  Official page ↗
                </a>
              )}
            </div>
          </div>

          {f.overview && <p className="mt-4 max-w-3xl text-sm leading-relaxed text-slate-700">{f.overview}</p>}
          <div className="mt-4"><SourceNote provenance={f.provenance} /></div>
        </div>
      </header>

      <Section title="Departments" subtitle="Every department inside this division.">
        {f.departments?.length > 0 ? (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {f.departments.map((d: Dict) => (
              <li key={d.id} className="card p-4">
                <p className="text-sm font-bold text-slate-900">{d.name}</p>
                {d.overview && <p className="mt-1 text-xs leading-relaxed text-slate-600">{d.overview}</p>}
                {d.research_areas?.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {d.research_areas.slice(0, 4).map((r: string) => (
                      <span key={r} className="chip bg-slate-100 text-[10px] text-slate-600">{r}</span>
                    ))}
                  </div>
                )}
                {d.website && (
                  <a href={d.website} target="_blank" rel="noopener noreferrer nofollow"
                    className="mt-2 inline-block text-xs font-semibold text-brand-800">Department site ↗</a>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No departments recorded yet" hint="Departments appear here once they are added in the admin." />
        )}
      </Section>

      {levels.length > 0 && (
        <Section title="Programmes" subtitle="Grouped by degree level." tone="soft">
          <div className="space-y-8">
            {levels.map((level) => (
              <div key={level}>
                <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-slate-500">
                  {LEVEL_LABEL[level] ?? level}
                  <span className="ml-2 font-normal normal-case text-slate-400">
                    ({data.programs_by_level[level].length})
                  </span>
                </h3>
                <Grid cols={3}>
                  {data.programs_by_level[level].map((p: Dict) => <ProgramCard key={p.id} p={p} />)}
                </Grid>
              </div>
            ))}
          </div>
        </Section>
      )}

      {data.professors?.length > 0 && (
        <Section title="Academic staff" subtitle="Supervisors and researchers attached to these departments.">
          <Grid cols={3}>
            {data.professors.map((p: Dict) => <FacultyCard key={p.id} f={p} />)}
          </Grid>
        </Section>
      )}
    </>
  );
}
