import type { Metadata } from "next";
import Link from "next/link";
import { api } from "@/lib/api";
import FacultyMatch from "@/components/faculty-match";
import { Disclaimer, FacultyCard, Grid, PageHeader, ProgramCard, Section } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "PhD and research supervisor discovery",
  description:
    "Find doctoral programmes and potential supervisors by research overlap. Every match shows the evidence behind it, and professor emails are published only where the university lists them officially.",
  alternates: { canonical: "/phd" },
};

export default async function PhdPage() {
  const [programs, faculty, countries, areas] = await Promise.all([
    api<Dict>("/programs?degree_level=PHD&page_size=9", 300),
    api<Dict>("/faculty?accepting=YES&page_size=9", 300),
    api<Dict[]>("/countries", 300),
    api<Dict[]>("/research-areas", 600),
  ]);

  return (
    <>
      <PageHeader
        eyebrow="PhD & research"
        title="Doctoral study starts with the right supervisor"
        description="A PhD application is a research conversation, not a form. Search doctoral programmes, find professors whose published work overlaps yours, and approach them with evidence instead of a mass email."
      >
        <div className="flex flex-wrap gap-3">
          <Link href="/programs?degree_level=PHD" className="rounded-xl bg-accent-500 px-5 py-3 text-sm font-bold text-brand-950">All PhD programmes</Link>
          <Link href="/scholarships?degree_level=PHD" className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25">Funded PhD positions</Link>
        </div>
      </PageHeader>

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <FacultyMatch countries={countries} areas={areas} />

        <div className="mt-6 grid gap-4 lg:grid-cols-3">
          {[
            { t: "1. Define the research problem", b: "A supervisor responds to a specific question, not 'I want to do a PhD in AI'. Write two or three sentences on the problem you want to work on and why it matters." },
            { t: "2. Prove the overlap", b: "Cite one of their papers and say what you would extend. Our match results give you the exact publications and keywords that connect your interests to theirs." },
            { t: "3. Contact correctly", b: "Use only the official address published on the department page. We never generate or guess an academic email address, and neither should you." },
          ].map((s) => (
            <div key={s.t} className="card p-5">
              <h3 className="text-base font-bold text-slate-900">{s.t}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{s.b}</p>
            </div>
          ))}
        </div>

        <div className="mt-6 card p-6">
          <h2 className="text-lg font-bold text-slate-900">Research areas we hold data for</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {areas.map((a) => (
              <span key={a.id} className="chip bg-brand-50 text-brand-800" title={(a.keywords ?? []).join(", ")}>
                {a.name}
              </span>
            ))}
          </div>
          <p className="mt-3 text-xs text-slate-500">
            Research areas are curated by the data team from official department pages. A missing area means we have
            not indexed it yet.
          </p>
        </div>

        <div className="mt-6"><Disclaimer>
          Faculty records are compiled from official department pages. Availability of supervision, funding and places
          changes constantly and is confirmed only by the professor or the graduate school. Emails are shown only when
          the institution publishes them; we never infer an address from a name pattern.
        </Disclaimer></div>
      </div>

      <Section title="Doctoral programmes" tone="soft" href="/programs?degree_level=PHD" hrefLabel="All PhD programmes">
        <Grid cols={3}>{(programs.items ?? []).map((p: Dict) => <ProgramCard key={p.id} p={p} />)}</Grid>
      </Section>

      <Section title="Professors currently accepting PhD students" subtitle="Status as recorded on the verification date shown on each profile.">
        <Grid cols={3}>{(faculty.items ?? []).map((f: Dict) => <FacultyCard key={f.id} f={f} />)}</Grid>
      </Section>
    </>
  );
}
