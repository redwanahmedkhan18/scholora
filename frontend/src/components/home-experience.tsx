"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LEVEL_LABEL, money, universityHref } from "@/lib/format";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

/**
 * The homepage rotation engine (spec §5).
 *
 *  - every slide is admin-controlled data, nothing is hard-coded here;
 *  - the cycle length is `duration_seconds` on each slide (default 30s from site settings);
 *  - rotation happens entirely client side: no page reload and no new network request
 *    per cycle - the whole payload was delivered once by the server component;
 *  - the next slide's media is preloaded while the current one is on screen;
 *  - the country-linked rail under the hero re-targets to the active slide's country,
 *    so featured universities / scholarships / stories change with the hero.
 */
export default function HomeExperience({ data }: { data: Dict }) {
  const hero = data.hero ?? {};
  const slides: Dict[] = useMemo(
    () => (hero.slides ?? []).filter((s: Dict) => s && s.title),
    [hero.slides],
  );
  const rotationEnabled: boolean = hero.rotation_enabled !== false && slides.length > 1;
  const fallbackDuration: number = hero.default_duration_seconds ?? 30;

  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [cycle, setCycle] = useState(0); // restarts the progress animation
  const touchX = useRef<number | null>(null);

  const active = slides[index] ?? null;
  const duration = Math.max(5, Number(active?.duration_seconds) || fallbackDuration);

  const go = useCallback(
    (next: number, manual = false) => {
      if (!slides.length) return;
      const n = (next + slides.length) % slides.length;
      setIndex(n);
      setCycle((c) => c + 1);
      if (manual) track("hero_slide_manual", { slide: slides[n]?.key, index: n });
    },
    [slides],
  );

  /* auto advance -------------------------------------------------------- */
  useEffect(() => {
    if (!rotationEnabled || paused) return;
    const t = window.setTimeout(() => go(index + 1), duration * 1000);
    return () => window.clearTimeout(t);
  }, [index, paused, duration, rotationEnabled, go, cycle]);

  /* pause when the tab is hidden - no wasted cycles --------------------- */
  useEffect(() => {
    const onVis = () => setPaused(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  /* keyboard ------------------------------------------------------------ */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") go(index + 1, true);
      if (e.key === "ArrowLeft") go(index - 1, true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, go]);

  /* preload the next slide's media -------------------------------------- */
  useEffect(() => {
    const next = slides[(index + 1) % Math.max(slides.length, 1)];
    const url = next?.background_media;
    if (!url) return;
    const img = new window.Image();
    img.src = url;
  }, [index, slides]);

  /* analytics ----------------------------------------------------------- */
  useEffect(() => {
    if (active) track("hero_slide_view", { slide: active.key, index, focus: active.focus });
  }, [active, index]);

  if (!active) return null;

  const countryId = active.country_id ?? null;
  const unis = filterByCountry(data.featured_universities, countryId, (u) => u.country?.id);
  const schols = filterByCountry(data.scholarships, countryId, (s) => s.country?.id);
  const stories = filterByCountry(data.success_stories, countryId, (s) => s.country?.id);
  const programs = filterByCountry(data.popular_programs, countryId, (p) => p.university?.country?.id);

  return (
    <>
      <section
        className="relative isolate overflow-hidden bg-brand-950"
        aria-roledescription="carousel"
        aria-label="Featured study destinations"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)}
        onBlur={() => setPaused(false)}
        onTouchStart={(e) => { touchX.current = e.touches[0].clientX; }}
        onTouchEnd={(e) => {
          if (touchX.current === null) return;
          const dx = e.changedTouches[0].clientX - touchX.current;
          if (Math.abs(dx) > 55) go(index + (dx < 0 ? 1 : -1), true);
          touchX.current = null;
        }}
      >
        {/* background layers - all mounted, only the active one is visible */}
        {slides.map((s, i) => (
          <div
            key={s.id ?? s.key ?? i}
            className="hero-layer"
            data-active={i === index}
            aria-hidden={i !== index}
            style={{
              background: s.background_media
                ? `linear-gradient(to right, rgba(7,24,58,.92), rgba(7,24,58,.55)), url(${s.background_media}) center/cover no-repeat`
                : s.gradient || "linear-gradient(135deg,#07183a 0%,#0f3d8a 55%,#f59e0b 100%)",
            }}
          />
        ))}
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(1200px_500px_at_15%_-10%,rgba(255,255,255,.16),transparent)]" aria-hidden />

        <div className="relative mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20 lg:py-24">
          <div className="grid items-center gap-10 lg:grid-cols-[1.15fr_0.85fr]">
            <div key={`copy-${index}`} className="hero-enter">
              <p className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-accent-400 ring-1 ring-white/15">
                {active.eyebrow ?? "Scholara"}
              </p>
              <h1 className="mt-4 text-3xl font-bold leading-[1.1] tracking-tight text-white sm:text-5xl">
                {active.title}
              </h1>
              {active.subtitle && <p className="mt-3 text-base font-medium text-accent-400 sm:text-lg">{active.subtitle}</p>}
              {active.description && (
                <p className="mt-4 max-w-xl text-sm leading-relaxed text-brand-100 sm:text-base">{active.description}</p>
              )}

              {active.highlights?.length > 0 && (
                <ul className="mt-5 flex flex-wrap gap-2">
                  {active.highlights.map((h: string) => (
                    <li key={h} className="rounded-full bg-white/10 px-3 py-1.5 text-xs font-medium text-white ring-1 ring-white/15">
                      {h}
                    </li>
                  ))}
                </ul>
              )}

              <div className="mt-7 flex flex-wrap gap-3">
                {active.cta_url && (
                  <Link
                    href={active.cta_url}
                    onClick={() => track("hero_cta_click", { slide: active.key, href: active.cta_url })}
                    className="rounded-xl bg-accent-500 px-5 py-3 text-sm font-bold text-brand-950 shadow-lg shadow-black/20 transition hover:bg-accent-400"
                  >
                    {active.cta_label ?? "Explore"}
                  </Link>
                )}
                {active.secondary_cta_url && (
                  <Link
                    href={active.secondary_cta_url}
                    onClick={() => track("hero_secondary_cta_click", { slide: active.key })}
                    className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25 backdrop-blur transition hover:bg-white/20"
                  >
                    {active.secondary_cta_label ?? "Check my eligibility"}
                  </Link>
                )}
              </div>
            </div>

            {/* live counters from the database */}
            <div key={`stats-${index}`} className="hero-enter grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-2">
              {statTiles(data.stats).map((s) => (
                <div key={s.label} className="rounded-2xl border border-white/15 bg-white/5 p-4 backdrop-blur">
                  <p className="text-2xl font-bold text-white">{s.value}</p>
                  <p className="mt-0.5 text-[11px] uppercase tracking-wide text-brand-100">{s.label}</p>
                </div>
              ))}
              <div className="col-span-2 rounded-2xl border border-white/15 bg-white/5 p-4 backdrop-blur sm:col-span-3 lg:col-span-2">
                <p className="text-[11px] leading-relaxed text-brand-100">
                  Every university, programme, fee and deadline on this site is stored with its source link and
                  verification date. We never publish a number we cannot point to.
                </p>
              </div>
            </div>
          </div>

          {/* controls */}
          <div className="mt-10 flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <button
                type="button" onClick={() => go(index - 1, true)} aria-label="Previous slide"
                className="grid h-9 w-9 place-items-center rounded-full bg-white/10 text-white ring-1 ring-white/20 transition hover:bg-white/20"
              >‹</button>
              <button
                type="button" onClick={() => go(index + 1, true)} aria-label="Next slide"
                className="grid h-9 w-9 place-items-center rounded-full bg-white/10 text-white ring-1 ring-white/20 transition hover:bg-white/20"
              >›</button>
              {rotationEnabled && (
                <button
                  type="button" onClick={() => setPaused((p) => !p)}
                  aria-label={paused ? "Resume rotation" : "Pause rotation"}
                  className="grid h-9 w-9 place-items-center rounded-full bg-white/10 text-white ring-1 ring-white/20 transition hover:bg-white/20"
                >{paused ? "▶" : "❚❚"}</button>
              )}
            </div>

            <div className="flex flex-1 flex-wrap items-center gap-1.5" role="tablist" aria-label="Choose a slide">
              {slides.map((s, i) => (
                <button
                  key={s.id ?? s.key ?? i}
                  role="tab"
                  aria-selected={i === index}
                  aria-label={s.title}
                  onClick={() => go(i, true)}
                  className={`h-1.5 rounded-full transition-all ${i === index ? "w-10 bg-accent-500" : "w-5 bg-white/30 hover:bg-white/60"}`}
                />
              ))}
            </div>

            <p className="text-[11px] text-brand-100">
              Slide {index + 1} / {slides.length} · rotates every {duration}s {paused && "· paused"}
            </p>
          </div>

          <div className="hero-progress mt-3 h-1 w-full overflow-hidden rounded-full bg-white/15">
            <span
              key={`${index}-${cycle}-${paused}`}
              style={{
                transition: paused || !rotationEnabled ? "none" : `transform ${duration}s linear`,
                transform: paused || !rotationEnabled ? "scaleX(0.02)" : "scaleX(1)",
                animation: "none",
              }}
              ref={(el) => {
                if (!el || paused || !rotationEnabled) return;
                el.style.transform = "scaleX(0)";
                requestAnimationFrame(() => requestAnimationFrame(() => { el.style.transform = "scaleX(1)"; }));
              }}
            />
          </div>
        </div>
      </section>

      {/* country-linked rail - re-targets with the hero, zero extra requests */}
      <section className="border-b border-slate-200 bg-white py-10 sm:py-14">
        <div className="mx-auto max-w-7xl px-4 sm:px-6">
          <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-brand-700">
                In focus right now
              </p>
              <h2 className="mt-1 text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
                {active.country_name
                  ? `Featured for ${active.country_name}`
                  : active.focus === "PHD" ? "Featured for PhD applicants"
                  : active.focus === "SCHOLARSHIP" ? "Featured funding"
                  : "Featured across all destinations"}
              </h2>
            </div>
            {active.cta_url && (
              <Link href={active.cta_url} className="text-sm font-semibold text-brand-800 hover:text-brand-600">
                {active.cta_label} →
              </Link>
            )}
          </div>

          <div key={`rail-${index}`} className="fade-in grid gap-4 lg:grid-cols-3">
            <RailCard title="Universities" href="/universities" empty="No universities recorded for this focus yet.">
              {unis.slice(0, 3).map((u) => (
                <li key={u.id}>
                  <Link href={universityHref(u)} className="group flex items-start justify-between gap-3 rounded-lg px-2 py-2 hover:bg-slate-50">
                    <span>
                      <span className="block text-sm font-semibold text-slate-900 group-hover:text-brand-800">{u.name}</span>
                      <span className="block text-[11px] text-slate-500">{u.country?.flag_emoji} {u.city} · {u.program_count} programmes</span>
                    </span>
                    <span className="shrink-0 text-[11px] font-semibold text-slate-500">{u.rankings?.[0]?.rank_overall ?? ""}</span>
                  </Link>
                </li>
              ))}
            </RailCard>

            <RailCard title="Scholarships" href="/scholarships" empty="No scholarships recorded for this focus yet.">
              {schols.slice(0, 3).map((s) => (
                <li key={s.id}>
                  <Link href={`/scholarships/${s.slug}`} className="group flex items-start justify-between gap-3 rounded-lg px-2 py-2 hover:bg-slate-50">
                    <span>
                      <span className="block text-sm font-semibold text-slate-900 group-hover:text-brand-800">{s.name}</span>
                      <span className="block text-[11px] text-slate-500">{s.provider}</span>
                    </span>
                    <span className="chip shrink-0 bg-accent-500/15 text-accent-600">
                      {s.amount_type === "FULL" ? "Full" : s.percentage ? `${s.percentage}%` : money(s.amount_value, s.currency, true)}
                    </span>
                  </Link>
                </li>
              ))}
            </RailCard>

            <RailCard title="Students placed" href="/success-stories" empty="No published stories for this focus yet.">
              {stories.slice(0, 3).map((s) => (
                <li key={s.id}>
                  <Link href={`/success-stories/${s.slug}`} className="group flex items-start justify-between gap-3 rounded-lg px-2 py-2 hover:bg-slate-50">
                    <span>
                      <span className="block text-sm font-semibold text-slate-900 group-hover:text-brand-800">{s.student_name}</span>
                      <span className="block text-[11px] text-slate-500">{s.program_name} · {s.university_name}</span>
                    </span>
                    <span className="shrink-0 text-[11px] font-semibold text-slate-500">{s.year}</span>
                  </Link>
                </li>
              ))}
            </RailCard>
          </div>

          {programs.length > 0 && (
            <div className="mt-6 overflow-x-auto scrollbar-none">
              <div className="flex gap-3 pb-1">
                {programs.slice(0, 8).map((p) => (
                  <Link
                    key={p.id}
                    href={`/programs/${p.slug}`}
                    className="card w-64 shrink-0 p-4"
                  >
                    <span className="chip bg-brand-800 text-white">{LEVEL_LABEL[p.degree_level] ?? p.degree_level}</span>
                    <p className="mt-2 line-clamp-2 text-sm font-bold text-slate-900">{p.name}</p>
                    <p className="mt-1 line-clamp-1 text-[11px] text-slate-500">{p.university?.name}</p>
                    <p className="mt-2 text-xs font-semibold text-slate-800">{money(p.tuition_annual, p.currency)} / year</p>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </>
  );
}

function RailCard({
  title, href, children, empty,
}: { title: string; href: string; children: React.ReactNode; empty: string }) {
  const items = Array.isArray(children) ? children : [children];
  const hasItems = items.flat().filter(Boolean).length > 0;
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-bold uppercase tracking-wide text-slate-700">{title}</h3>
        <Link href={href} className="text-[11px] font-semibold text-brand-800 hover:text-brand-600">All →</Link>
      </div>
      {hasItems ? <ul className="divide-y divide-slate-100">{children}</ul> : <p className="px-2 py-4 text-xs text-slate-500">{empty}</p>}
    </div>
  );
}

function filterByCountry(list: Dict[] | undefined, countryId: number | null, pick: (x: Dict) => number | undefined) {
  const all = list ?? [];
  if (!countryId) return all;
  const matched = all.filter((x) => pick(x) === countryId);
  return matched.length ? matched : all;
}

function statTiles(stats: Dict = {}) {
  return [
    { label: "Universities", value: stats.universities ?? 0 },
    { label: "Programmes", value: stats.programs ?? 0 },
    { label: "Destinations", value: stats.countries ?? 0 },
    { label: "Scholarships", value: stats.scholarships ?? 0 },
  ];
}
