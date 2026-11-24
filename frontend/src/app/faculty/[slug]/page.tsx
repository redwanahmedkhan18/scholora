import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { apiOrNull } from "@/lib/api";
import { EMAIL_STATUS_LABEL, universityHref } from "@/lib/format";
import { Disclaimer, SourceNote } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;
type Props = { params: Promise<{ slug: string }> };

export async function generateStaticParams() {
  const data = await apiOrNull<Dict>("/sitemap-data", 600);
  return (data?.faculty ?? []).map((f: Dict) => ({ slug: f.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/faculty/${slug}`, 300);
  if (!data) return { title: "Professor not found" };
  const p = data.professor;
  return {
    title: `${p.full_name} — ${p.university?.name}`,
    description: `${p.full_name}, ${p.title} at ${p.university?.name}. Research interests, publications, PhD supervision status and official contact route.`,
    alternates: { canonical: `/faculty/${p.slug}` },
  };
}

export default async function FacultyPage({ params }: Props) {
  const { slug } = await params;
  const data = await apiOrNull<Dict>(`/faculty/${slug}`, 300);
  if (!data) notFound();
  const p = data.professor;
  const pubs: Dict[] = data.publications ?? [];

  const emailLabel = EMAIL_STATUS_LABEL[p.email_status] ?? "Not publicly available";

  return (
    <>
      <header className="bg-gradient-to-br from-brand-950 via-brand-800 to-brand-700 text-white">
        <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 sm:py-14">
          <nav aria-label="Breadcrumb" className="text-xs text-brand-100">
            <Link href="/" className="hover:text-white">Home</Link> /{" "}
            <Link href="/phd" className="hover:text-white">PhD &amp; research</Link> / <span className="text-white">{p.full_name}</span>
          </nav>
          <h1 className="mt-4 text-3xl font-bold tracking-tight sm:text-4xl">{p.full_name}</h1>
          <p className="mt-2 text-sm font-medium text-accent-400">
            {p.title} · {p.university?.country?.flag_emoji}{" "}
            <Link href={universityHref(p.university)} className="underline underline-offset-2">{p.university?.name}</Link>
            {p.department?.name ? ` · ${p.department.name}` : ""}
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <span className={`chip ring-1 ${p.accepting_phd === "YES" ? "bg-emerald-500/20 text-emerald-100 ring-emerald-300/40" : "bg-white/10 text-white ring-white/20"}`}>
              PhD supervision: {p.accepting_phd ?? "Not stated"}
            </span>
            {p.lab_name && <span className="chip bg-white/10 text-white ring-1 ring-white/20">{p.lab_name}</span>}
            {typeof p.h_index === "number" && <span className="chip bg-white/10 text-white ring-1 ring-white/20">h-index {p.h_index}</span>}
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
          <div className="space-y-6">
            {p.bio && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Profile</h2>
                <p className="mt-2 text-sm leading-relaxed text-slate-700">{p.bio}</p>
              </div>
            )}

            <div className="card p-6">
              <h2 className="text-lg font-bold text-slate-900">Research</h2>
              <h3 className="mt-3 text-xs font-bold uppercase tracking-wide text-slate-500">Interests</h3>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(p.research_interests ?? []).map((r: string) => <span key={r} className="chip bg-brand-50 text-brand-800">{r}</span>)}
              </div>
              {(p.research_keywords ?? []).length > 0 && (
                <>
                  <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-slate-500">Keywords</h3>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {p.research_keywords.map((r: string) => <span key={r} className="chip bg-slate-100 text-slate-700">{r}</span>)}
                  </div>
                </>
              )}
              {p.supervision_notes && (
                <p className="mt-4 rounded-lg bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">{p.supervision_notes}</p>
              )}
              {p.funding_notes && <p className="mt-2 text-xs text-slate-600">{p.funding_notes}</p>}
              <SourceNote provenance={p.provenance} label="Profile source" />
            </div>

            {pubs.length > 0 && (
              <div className="card p-6">
                <h2 className="text-lg font-bold text-slate-900">Selected publications</h2>
                <ul className="mt-3 divide-y divide-slate-100">
                  {pubs.map((pub) => (
                    <li key={pub.id ?? pub.title} className="py-3">
                      <p className="text-sm font-semibold text-slate-900">{pub.title}</p>
                      <p className="mt-0.5 text-xs text-slate-600">{pub.venue} · {pub.year}</p>
                      {(pub.keywords ?? []).length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {pub.keywords.map((k: string) => <span key={k} className="chip bg-slate-100 text-[10px] text-slate-600">{k}</span>)}
                        </div>
                      )}
                      {pub.url && <a href={pub.url} target="_blank" rel="noopener noreferrer nofollow" className="mt-1 inline-block text-xs font-semibold text-brand-800">View publication ↗</a>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <aside className="space-y-6">
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Contact</h2>
              <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
                {p.email ? (
                  <>
                    <a href={`mailto:${p.email}`} className="break-all text-sm font-bold text-brand-800">{p.email}</a>
                    <p className="mt-1 text-[11px] font-semibold uppercase tracking-wide text-emerald-700">{emailLabel}</p>
                    {p.email_source_url && (
                      <a href={p.email_source_url} target="_blank" rel="noopener noreferrer nofollow" className="mt-1 inline-block text-[11px] text-slate-600 underline">
                        Where this address is published ↗
                      </a>
                    )}
                  </>
                ) : (
                  <>
                    <p className="text-sm font-bold text-slate-700">No public address</p>
                    <p className="mt-1 text-[11px] text-slate-600">{emailLabel}. Use the departmental contact form or the graduate admissions office instead.</p>
                  </>
                )}
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
                We publish academic contact details only when the university or the professor has made them public.
                Addresses are never inferred from a name pattern, and we do not sell or bulk-export them.
              </p>
              <div className="mt-4 space-y-1.5 text-sm">
                {p.profile_url && <p><a href={p.profile_url} target="_blank" rel="noopener noreferrer nofollow" className="font-semibold text-brand-800">University profile ↗</a></p>}
                {p.lab_url && <p><a href={p.lab_url} target="_blank" rel="noopener noreferrer nofollow" className="font-semibold text-brand-800">Research group ↗</a></p>}
                {p.google_scholar && <p><a href={p.google_scholar} target="_blank" rel="noopener noreferrer nofollow" className="font-semibold text-brand-800">Google Scholar ↗</a></p>}
              </div>
            </div>

            <div className="card bg-brand-950 p-6 text-white">
              <h2 className="text-base font-bold">Write a better first email</h2>
              <p className="mt-2 text-sm leading-relaxed text-brand-100">
                Our counselors review your research statement and the overlap evidence before you send anything. One
                well-targeted email beats fifty generic ones.
              </p>
              <Link href="/appointments" className="mt-4 block rounded-xl bg-accent-500 px-4 py-2.5 text-center text-sm font-bold text-brand-950">
                Book a PhD session
              </Link>
            </div>

            <Disclaimer />
          </aside>
        </div>
      </div>
    </>
  );
}
