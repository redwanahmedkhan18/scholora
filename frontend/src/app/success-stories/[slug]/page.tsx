import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { LEVEL_LABEL, dateLabel } from "@/lib/format";
import { Grid, StoryCard } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.success_stories ?? []).map((s: Dict) => ({ slug: s.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const s = await apiOrNull<Dict>(`/success-stories/${slug}`, 300);
  if (!s) return { title: "Story not found" };
  return {
    title: s.seo?.title ?? s.headline,
    description: s.seo?.description ?? `${s.student_name} secured ${s.program_name} at ${s.university_name} for ${s.intake}.`,
    alternates: { canonical: `/success-stories/${s.slug}` },
  };
}

export default async function StoryPage({ params }: Props) {
  const { slug } = await params;
  const s = await apiOrNull<Dict>(`/success-stories/${slug}`, 300);
  if (!s) notFound();
  const more = await apiOrNull<Dict>(`/success-stories?country=${s.country?.slug ?? ""}&page_size=4`, 300);
  const related = (more?.items ?? []).filter((x: Dict) => x.slug !== s.slug).slice(0, 3);

  return (
    <>
      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/success-stories" className="hover:text-white">Success stories</Link> / <span className="text-white">{s.student_name}</span>
          </nav>
          <h1 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl">{s.headline}</h1>
          <p className="mt-2 text-sm font-medium text-accent-400">
            {s.country?.flag_emoji} {s.student_name} · {s.intake} · {LEVEL_LABEL[s.degree_level] ?? s.degree_level}
          </p>
        </div>
      </header>

      <article className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            ["Programme", s.program_name], ["University", s.university_name],
            ["Scholarship", s.scholarship ?? "None recorded"], ["Visa", s.visa_result ?? "Not recorded"],
          ].map(([k, v]) => (
            <div key={k as string} className="card p-4">
              <dt className="text-[11px] uppercase tracking-wide text-slate-500">{k}</dt>
              <dd className="mt-0.5 text-sm font-bold text-slate-900">{String(v)}</dd>
            </div>
          ))}
        </dl>

        {s.testimonial && (
          <blockquote className="card mt-6 border-l-4 border-accent-500 p-6 text-lg italic leading-relaxed text-slate-800">
            “{s.testimonial}”
            <footer className="mt-3 text-sm not-italic font-semibold text-slate-600">— {s.student_name}, {s.year}</footer>
          </blockquote>
        )}

        {(s.journey ?? []).length > 0 && (
          <div className="card mt-6 p-6">
            <h2 className="text-lg font-bold text-slate-900">The journey</h2>
            <ol className="mt-4 space-y-3">
              {s.journey.map((j: string, i: number) => (
                <li key={j} className="flex items-center gap-3">
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand-800 text-xs font-bold text-white">{i + 1}</span>
                  <span className="text-sm font-medium text-slate-800">{j}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        <p className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs leading-relaxed text-slate-600">
          Published with written consent (reference {s.consent_reference ?? "on file"}) on {dateLabel(s.published_at)}.
          Individual results depend on the student&apos;s own profile, the university&apos;s decision and the visa
          authority. Past outcomes do not predict future ones.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/profile-assessment" className="rounded-xl bg-brand-800 px-5 py-3 text-sm font-bold text-white">Check my own eligibility</Link>
          <Link href="/appointments" className="rounded-xl border border-slate-300 px-5 py-3 text-sm font-bold text-slate-700">Book a counselor</Link>
        </div>

        {related.length > 0 && (
          <section className="mt-12">
            <h2 className="mb-4 text-xl font-bold text-slate-900">More stories from {s.country?.name}</h2>
            <Grid cols={3}>{related.map((x: Dict) => <StoryCard key={x.id} s={x} />)}</Grid>
          </section>
        )}
      </article>
    </>
  );
}
