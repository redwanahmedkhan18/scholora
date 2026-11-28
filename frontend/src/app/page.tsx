import Link from "next/link";
import type { Metadata } from "next";
import { api, getOptions, getSettings } from "@/lib/api";
import { dateLabel } from "@/lib/format";
import HomeExperience from "@/components/home-experience";
import QuickSearch from "@/components/quick-search";
import { FacebookPanel } from "@/components/site-chrome";
import {
  CountryCard, EventCard, FacultyCard, Grid, PostCard, ProgramCard,
  ScholarshipCard, Section, ServiceCard, StoryCard, UniversityCard,
} from "@/components/ui";
import { jsonLdScript } from "@/lib/json-ld";

export const revalidate = 60;

type Dict = Record<string, any>;

export async function generateMetadata(): Promise<Metadata> {
  const s = await getSettings();
  return {
    title: s.default_seo_title ?? "Scholara",
    description: s.default_seo_description,
    alternates: { canonical: "/" },
  };
}

export default async function HomePage() {
  const [data, settings, degreeLevels] = await Promise.all([
    api<Dict>("/homepage", 30), getSettings(), getOptions("degree_levels"),
  ]);
  // A section renders only when it is enabled AND has something to show. "why_us" and
  // "journey" are pure CMS content: if an admin empties them, the section disappears rather
  // than leaving a titled, empty band on the homepage.
  const hasContent = (s: Dict) =>
    (s.key !== "why_us" || (s.config?.items ?? []).length > 0) &&
    (s.key !== "journey" || (s.config?.steps ?? []).length > 0);
  const sections: Dict[] = (data.sections ?? []).filter((s: Dict) => s.is_enabled && hasContent(s));
  const get = (key: string) => sections.find((s) => s.key === key);

  const website = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: settings.brand_name,
    url: settings.site_url,
    potentialAction: {
      "@type": "SearchAction",
      target: `${settings.site_url}/search?q={search_term_string}`,
      "query-input": "required name=search_term_string",
    },
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(website) }} />

      {/* §5 rotating hero + country-linked rail, all client-side after first load */}
      <HomeExperience data={data} />

      {get("search") && (
        <div className="relative z-10">
          <QuickSearch countries={data.countries ?? []} degreeLevels={degreeLevels} title={get("search")!.title} subtitle={get("search")!.subtitle} />
        </div>
      )}

      {sections.map((section) => {
        const limit = section.config?.limit ?? 8;
        switch (section.key) {
          case "countries":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle}
                href="/study-destinations" hrefLabel="All destinations">
                <Grid cols={4}>
                  {(data.countries ?? []).slice(0, limit).map((c: Dict) => <CountryCard key={c.id} c={c} />)}
                </Grid>
              </Section>
            );

          case "featured_universities":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} tone="soft"
                href="/universities" hrefLabel="Search all universities">
                <Grid cols={3}>
                  {(data.featured_universities ?? []).slice(0, limit).map((u: Dict) => <UniversityCard key={u.id} u={u} />)}
                </Grid>
              </Section>
            );

          case "popular_programs":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle}
                href="/programs" hrefLabel="Programme search">
                <Grid cols={3}>
                  {(data.popular_programs ?? []).slice(0, limit).map((p: Dict) => <ProgramCard key={p.id} p={p} />)}
                </Grid>
              </Section>
            );

          case "scholarships":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} tone="soft"
                href="/scholarships" hrefLabel="All scholarships">
                <Grid cols={4}>
                  {(data.scholarships ?? []).slice(0, limit).map((s: Dict) => <ScholarshipCard key={s.id} s={s} />)}
                </Grid>
              </Section>
            );

          case "why_us":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle}>
                <Grid cols={3}>
                  {((section.config?.items ?? []) as Dict[]).map((w) => (
                    <div key={w.title} className="card p-5">
                      {w.icon && <span className="text-2xl" aria-hidden>{w.icon}</span>}
                      <h3 className="mt-3 text-base font-bold text-slate-900">{w.title}</h3>
                      <p className="mt-2 text-sm leading-relaxed text-slate-600">{w.body}</p>
                    </div>
                  ))}
                </Grid>
              </Section>
            );

          case "services":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} tone="soft"
                href="/services" hrefLabel="All services">
                <Grid cols={4}>
                  {(data.services ?? []).slice(0, limit).map((s: Dict) => <ServiceCard key={s.id} s={s} />)}
                </Grid>
              </Section>
            );

          case "success_stories":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle}
                href="/success-stories" hrefLabel="All stories">
                <Grid cols={3}>
                  {(data.success_stories ?? []).slice(0, limit).map((s: Dict) => <StoryCard key={s.id} s={s} />)}
                </Grid>
              </Section>
            );

          case "events":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} tone="soft" href="/events">
                <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
                  <Grid cols={2}>
                    {(data.events ?? []).slice(0, limit).map((e: Dict) => <EventCard key={e.id} e={e} />)}
                  </Grid>
                  <FacebookPanel settings={settings} />
                </div>
              </Section>
            );

          case "phd":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} href="/phd" hrefLabel="PhD hub">
                <div className="grid gap-4 lg:grid-cols-2">
                  <div>
                    <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-slate-600">Doctoral programmes</h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {(data.phd?.programs ?? []).slice(0, limit).map((p: Dict) => <ProgramCard key={p.id} p={p} compact />)}
                    </div>
                  </div>
                  <div>
                    <h3 className="mb-3 text-sm font-bold uppercase tracking-wide text-slate-600">Potential supervisors</h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {(data.phd?.professors ?? []).slice(0, limit).map((f: Dict) => <FacultyCard key={f.id} f={f} />)}
                    </div>
                  </div>
                </div>
              </Section>
            );

          case "journey":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} tone="soft">
                <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {((section.config?.steps ?? []) as Dict[]).map((j, i) => (
                    <li key={j.title} className="card relative p-5">
                      <span className="absolute right-4 top-4 text-3xl font-black text-slate-100">{i + 1}</span>
                      <h3 className="text-base font-bold text-slate-900">{j.title}</h3>
                      <p className="mt-2 text-sm leading-relaxed text-slate-600">{j.body}</p>
                    </li>
                  ))}
                </ol>
              </Section>
            );

          case "articles":
            return (
              <Section key={section.key} title={section.title} subtitle={section.subtitle} href="/blog">
                <Grid cols={3}>
                  {(data.posts ?? []).slice(0, limit).map((p: Dict) => <PostCard key={p.id} p={p} />)}
                </Grid>
              </Section>
            );

          case "cta":
            return (
              <section key={section.key} className="bg-brand-950 py-14 text-white sm:py-20">
                <div className="mx-auto grid max-w-7xl items-center gap-8 px-4 sm:px-6 lg:grid-cols-[1.3fr_1fr]">
                  <div>
                    <h2 className="text-2xl font-bold tracking-tight sm:text-4xl">{section.title}</h2>
                    {section.subtitle && <p className="mt-3 max-w-2xl text-sm leading-relaxed text-brand-100 sm:text-base">{section.subtitle}</p>}
                    <div className="mt-6 flex flex-wrap gap-3">
                      <Link href="/profile-assessment" className="rounded-xl bg-accent-500 px-5 py-3 text-sm font-bold text-brand-950 hover:bg-accent-400">
                        Start the free assessment
                      </Link>
                      <Link href="/appointments" className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
                        Book an office visit
                      </Link>
                      <Link href="/contact" className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
                        Contact options
                      </Link>
                    </div>
                    <p className="mt-5 text-xs text-brand-100">
                      {settings.office_address} · {settings.office_hours}
                    </p>
                  </div>
                  <dl className="grid grid-cols-2 gap-3">
                    {Object.entries(data.stats ?? {}).map(([k, v]) => (
                      <div key={k} className="rounded-2xl border border-white/15 bg-white/5 p-4">
                        <dt className="text-[11px] uppercase tracking-wide text-brand-100">{k.replace(/_/g, " ")}</dt>
                        <dd className="text-2xl font-bold">{String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </section>
            );

          default:
            return null;
        }
      })}

      <div className="mx-auto max-w-7xl px-4 pb-12 text-center text-xs text-slate-500 sm:px-6">
        Homepage content, slide order and rotation timing are managed in the admin CMS. Content snapshot generated{" "}
        {dateLabel(data.hero?.generated_at)}.
      </div>
    </>
  );
}


