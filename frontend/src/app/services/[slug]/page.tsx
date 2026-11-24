import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { Grid, ServiceCard } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.services ?? []).map((s: Dict) => ({ slug: s.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const s = await apiOrNull<Dict>(`/services/${slug}`, 300);
  if (!s) return { title: "Service not found" };
  return {
    title: s.seo?.title ?? s.title,
    description: s.seo?.description ?? s.summary,
    alternates: { canonical: `/services/${s.slug}` },
  };
}

export default async function ServicePage({ params }: Props) {
  const { slug } = await params;
  const [s, all] = await Promise.all([
    apiOrNull<Dict>(`/services/${slug}`, 300),
    apiOrNull<Dict[]>("/services", 300),
  ]);
  if (!s) notFound();
  const related = (all ?? []).filter((x) => x.slug !== s.slug && x.category === s.category).slice(0, 4);

  const faqLd = {
    "@context": "https://schema.org", "@type": "FAQPage",
    mainEntity: (s.faqs ?? []).map((f: Dict) => ({
      "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a },
    })),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqLd) }} />
      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/services" className="hover:text-white">Services</Link> / <span className="text-white">{s.title}</span>
          </nav>
          <h1 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl">{s.title}</h1>
          <p className="mt-3 max-w-3xl text-sm leading-relaxed text-brand-100">{s.summary}</p>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
          <div className="space-y-6">
            <div className="card p-6">
              <h2 className="text-lg font-bold text-slate-900">What this covers</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-700">{s.description}</p>
            </div>

            {(s.process ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">How it works</h2>
                <ol className="mt-3 space-y-3">
                  {s.process.map((p: Dict) => (
                    <li key={p.step} className="flex gap-3">
                      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand-800 text-xs font-bold text-white">{p.step}</span>
                      <div>
                        <p className="text-sm font-semibold text-slate-900">{p.title}</p>
                        <p className="text-sm text-slate-600">{p.detail}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {(s.faqs ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Questions</h2>
                <dl className="mt-3 divide-y divide-slate-100">
                  {s.faqs.map((f: Dict) => (
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
            {(s.benefits ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-base font-bold text-slate-900">What you get</h2>
                <ul className="mt-3 space-y-2 text-sm text-slate-700">
                  {s.benefits.map((b: string) => <li key={b} className="flex gap-2"><span className="text-brand-700" aria-hidden>✓</span>{b}</li>)}
                </ul>
              </div>
            )}
            {(s.documents ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-base font-bold text-slate-900">Bring with you</h2>
                <ul className="mt-3 space-y-2 text-sm text-slate-700">
                  {s.documents.map((d: string) => <li key={d} className="flex gap-2"><span className="text-brand-700" aria-hidden>▢</span>{d}</li>)}
                </ul>
              </div>
            )}
            {(s.countries ?? []).length > 0 && (
              <div className="card p-6">
                <h2 className="text-base font-bold text-slate-900">Available for</h2>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {s.countries.map((c: string) => <span key={c} className="chip bg-slate-100 text-slate-700">{c}</span>)}
                </div>
              </div>
            )}
            <div className="card bg-brand-950 p-6 text-white">
              <h2 className="text-base font-bold">Start this service</h2>
              <p className="mt-2 text-sm leading-relaxed text-brand-100">
                Any chargeable service is quoted in writing before work begins. The first counseling session is free.
              </p>
              <div className="mt-4 space-y-2">
                <Link href="/appointments" className="block rounded-xl bg-accent-500 px-4 py-2.5 text-center text-sm font-bold text-brand-950">Book a session</Link>
                <Link href="/contact" className="block rounded-xl bg-white/10 px-4 py-2.5 text-center text-sm font-bold text-white ring-1 ring-white/20">Contact options</Link>
              </div>
            </div>
          </aside>
        </div>

        {related.length > 0 && (
          <section className="mt-12">
            <h2 className="mb-4 text-xl font-bold text-slate-900">Related services</h2>
            <Grid cols={4}>{related.map((x) => <ServiceCard key={x.id} s={x} />)}</Grid>
          </section>
        )}
      </div>
    </>
  );
}
