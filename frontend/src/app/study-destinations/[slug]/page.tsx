import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { api, apiOrNull } from "@/lib/api";
import { range } from "@/lib/format";
import {
  Breadcrumbs, Disclaimer, EventCard, Grid, ProgramCard, ScholarshipCard,
  Section, StoryCard, UniversityCard,
} from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.countries ?? []).map((c: Dict) => ({ slug: c.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/countries/${slug}`, 300);
  if (!data) return { title: "Destination not found" };
  const c = data.country;
  return {
    title: c.seo?.title ?? `Study in ${c.name}`,
    description: c.seo?.description ?? c.summary,
    alternates: { canonical: `/study-destinations/${c.slug}` },
    openGraph: { title: `Study in ${c.name} from Bangladesh`, description: c.tagline, type: "article" },
  };
}

export default async function CountryPage({ params }: Props) {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/countries/${slug}`, 300);
  if (!data) notFound();
  const c = data.country;

  const faqLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: (c.faqs ?? []).map((f: Dict) => ({
      "@type": "Question", name: f.q,
      acceptedAnswer: { "@type": "Answer", text: f.a },
    })),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqLd) }} />

      <header className="relative overflow-hidden text-white"
        style={{ background: "linear-gradient(135deg,#07183a 0%,#0f3d8a 58%,#1f3fe0 100%)" }}>
        <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 sm:py-16">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> / <Link href="/study-destinations" className="hover:text-white">Destinations</Link> / <span className="text-white">{c.name}</span>
          </nav>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span className="text-5xl" aria-hidden>{c.flag_emoji}</span>
            <div>
              <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Study in {c.name}</h1>
              <p className="mt-1 text-sm font-medium text-accent-400 sm:text-base">{c.tagline}</p>
            </div>
          </div>
          <p className="mt-4 max-w-3xl text-sm leading-relaxed text-brand-100">{c.summary}</p>

          <dl className="mt-7 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["Tuition / year", range(c.tuition_min, c.tuition_max, c.currency)],
              ["Living / year", range(c.living_cost_min, c.living_cost_max, c.currency)],
              ["Universities", c.counts?.universities ?? 0],
              ["Programmes", c.counts?.programs ?? 0],
            ].map(([k, v]) => (
              <div key={k as string} className="rounded-2xl border border-white/15 bg-white/5 p-4 backdrop-blur">
                <dt className="text-[11px] uppercase tracking-wide text-brand-100">{k}</dt>
                <dd className="mt-0.5 text-base font-bold text-white">{v}</dd>
              </div>
            ))}
          </dl>

          <div className="mt-7 flex flex-wrap gap-3">
            <Link href={`/universities?country=${c.slug}`} className="rounded-xl bg-accent-500 px-5 py-3 text-sm font-bold text-brand-950 hover:bg-accent-400">
              Browse {c.name} universities
            </Link>
            <Link href={`/programs?country=${c.slug}`} className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
              Programme search
            </Link>
            <Link href="/profile-assessment" className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
              Check my eligibility
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
          <div className="space-y-6">
            {c.why_study?.length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Why students choose {c.name}</h2>
                <ul className="mt-3 space-y-2">
                  {c.why_study.map((w: string) => (
                    <li key={w} className="flex gap-2 text-sm leading-relaxed text-slate-700">
                      <span className="mt-0.5 text-brand-700" aria-hidden>✓</span>{w}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="card p-6">
              <h2 className="text-lg font-bold text-slate-900">Visa, work and student life</h2>
              <div className="mt-3 space-y-3 text-sm leading-relaxed text-slate-700">
                <p><strong className="text-slate-900">Student visa.</strong> {c.visa_summary}</p>
                <p><strong className="text-slate-900">Work rights.</strong> {c.work_rights}</p>
                <p><strong className="text-slate-900">After graduation.</strong> {c.post_study_work}</p>
                <p><strong className="text-slate-900">Student life.</strong> {c.student_life}</p>
              </div>
              {c.visa_resources?.length > 0 && (
                <ul className="mt-4 space-y-1.5">
                  {c.visa_resources.map((r: Dict) => (
                    <li key={r.url}>
                      <a href={r.url} target="_blank" rel="noopener noreferrer nofollow" className="text-sm font-semibold text-brand-800 underline underline-offset-2">
                        {r.label} ↗
                      </a>
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-4"><Disclaimer /></div>
            </div>

            {c.faqs?.length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">{c.name} FAQs</h2>
                <dl className="mt-3 divide-y divide-slate-100">
                  {c.faqs.map((f: Dict) => (
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
              <h2 className="text-base font-bold text-slate-900">Key facts</h2>
              <dl className="mt-3 space-y-2.5 text-sm">
                {[
                  ["Region", c.region],
                  ["Currency", c.currency],
                  ["Intakes", (c.intakes ?? []).join(", ") || "—"],
                  ["Popular subjects", (c.popular_subjects ?? []).join(", ") || "—"],
                  ["Scholarships recorded", c.counts?.scholarships ?? 0],
                  ["Students placed", c.counts?.success_stories ?? 0],
                ].map(([k, v]) => (
                  <div key={k as string} className="flex gap-3">
                    <dt className="w-36 shrink-0 text-slate-500">{k}</dt>
                    <dd className="font-medium text-slate-900">{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="card bg-brand-950 p-6 text-white">
              <h2 className="text-base font-bold">Talk it through</h2>
              <p className="mt-2 text-sm leading-relaxed text-brand-100">
                Bring your transcripts and test score to the Uttara office, or send them on WhatsApp. We will tell you
                what you qualify for in {c.name} — and what you do not.
              </p>
              <div className="mt-4 space-y-2">
                <Link href="/appointments" className="block rounded-xl bg-accent-500 px-4 py-2.5 text-center text-sm font-bold text-brand-950">Book a counselor</Link>
                <Link href="/contact" className="block rounded-xl bg-white/10 px-4 py-2.5 text-center text-sm font-bold text-white ring-1 ring-white/20">Contact options</Link>
              </div>
            </div>
          </aside>
        </div>
      </div>

      {data.universities?.length > 0 && (
        <Section title={`Universities in ${c.name}`} tone="soft" href={`/universities?country=${c.slug}`} hrefLabel="See all">
          <Grid cols={3}>{data.universities.slice(0, 6).map((u: Dict) => <UniversityCard key={u.id} u={u} />)}</Grid>
        </Section>
      )}

      {data.programs?.length > 0 && (
        <Section title={`Popular programmes in ${c.name}`} href={`/programs?country=${c.slug}`} hrefLabel="Programme search">
          <Grid cols={3}>{data.programs.slice(0, 6).map((p: Dict) => <ProgramCard key={p.id} p={p} />)}</Grid>
        </Section>
      )}

      {data.scholarships?.length > 0 && (
        <Section title={`Scholarships for ${c.name}`} tone="soft" href={`/scholarships?country=${c.slug}`} hrefLabel="All scholarships">
          <Grid cols={4}>{data.scholarships.slice(0, 4).map((s: Dict) => <ScholarshipCard key={s.id} s={s} />)}</Grid>
        </Section>
      )}

      {data.success_stories?.length > 0 && (
        <Section title={`Students we placed in ${c.name}`} href={`/success-stories?country=${c.slug}`} hrefLabel="All stories">
          <Grid cols={3}>{data.success_stories.slice(0, 3).map((s: Dict) => <StoryCard key={s.id} s={s} />)}</Grid>
        </Section>
      )}

      {data.events?.length > 0 && (
        <Section title="Upcoming events" tone="soft" href="/events">
          <Grid cols={3}>{data.events.map((e: Dict) => <EventCard key={e.id} e={e} />)}</Grid>
        </Section>
      )}
    </>
  );
}
