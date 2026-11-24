"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { track, whatsappHref } from "@/lib/track";

type Dict = Record<string, any>;

const PRIMARY = [
  { href: "/study-destinations", label: "Destinations" },
  { href: "/universities", label: "Universities" },
  { href: "/programs", label: "Programmes" },
  { href: "/scholarships", label: "Scholarships" },
  { href: "/phd", label: "PhD & Research" },
  { href: "/services", label: "Services" },
];

const MORE = [
  { href: "/success-stories", label: "Success stories" },
  { href: "/events", label: "Events" },
  { href: "/blog", label: "Blog" },
  { href: "/faqs", label: "FAQs" },
  { href: "/compare", label: "Compare" },
  { href: "/shortlist", label: "My shortlist" },
  { href: "/contact", label: "Contact" },
];

export default function SiteHeader({ settings, countries }: { settings: Dict; countries: Dict[] }) {
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [dest, setDest] = useState(false);
  const [q, setQ] = useState("");
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => { setOpen(false); setMore(false); setDest(false); }, [pathname]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    track("site_search", { query: q });
    router.push(`/search?q=${encodeURIComponent(q.trim())}`);
  };

  const active = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  return (
    <header className="sticky top-0 z-50 border-b border-slate-200 bg-white/95 backdrop-blur">
      <div className="hidden bg-brand-950 text-white lg:block">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-1.5 text-[11px]">
          <p>{settings.office_address}</p>
          <div className="flex items-center gap-4">
            <span>{settings.office_hours}</span>
            {settings.phones?.[0] && <a href={`tel:${settings.phones[0]}`} className="font-semibold hover:text-accent-400">{settings.phones[0]}</a>}
            {settings.emails?.[0] && <a href={`mailto:${settings.emails[0]}`} className="font-semibold hover:text-accent-400">{settings.emails[0]}</a>}
            {settings.facebook_page_url && (
              <a href={settings.facebook_page_url} target="_blank" rel="noopener noreferrer"
                 onClick={() => track("facebook_page_click", { placement: "topbar" })}
                 className="font-semibold hover:text-accent-400">Facebook</a>
            )}
          </div>
        </div>
      </div>

      <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3 sm:px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand-800 text-sm font-black text-white">S</span>
          <span className="leading-tight">
            <span className="block text-base font-black tracking-tight text-brand-900">{settings.brand_name ?? "Scholara"}</span>
            <span className="hidden text-[10px] font-medium uppercase tracking-wider text-slate-500 sm:block">Study abroad intelligence</span>
          </span>
        </Link>

        <nav className="ml-4 hidden items-center gap-1 xl:flex">
          <div className="relative" onMouseEnter={() => setDest(true)} onMouseLeave={() => setDest(false)}>
            <Link href="/study-destinations"
              className={`rounded-lg px-3 py-2 text-sm font-semibold transition ${active("/study-destinations") ? "bg-brand-50 text-brand-800" : "text-slate-700 hover:bg-slate-100"}`}>
              Destinations ▾
            </Link>
            {dest && (
              <div className="absolute left-0 top-full w-[34rem] rounded-xl border border-slate-200 bg-white p-3 shadow-xl">
                <div className="grid grid-cols-2 gap-1">
                  {countries.slice(0, 12).map((c) => (
                    <Link key={c.id} href={`/study-destinations/${c.slug}`}
                      className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-slate-700 hover:bg-slate-50">
                      <span aria-hidden>{c.flag_emoji}</span>
                      <span className="truncate font-medium">{c.name}</span>
                      {c.status === "COMING_SOON" && <span className="chip ml-auto bg-slate-100 text-[10px] text-slate-500">Soon</span>}
                    </Link>
                  ))}
                </div>
                <Link href="/study-destinations" className="mt-2 block rounded-lg bg-slate-50 px-3 py-2 text-center text-xs font-semibold text-brand-800">
                  Compare all destinations →
                </Link>
              </div>
            )}
          </div>

          {PRIMARY.slice(1).map((l) => (
            <Link key={l.href} href={l.href}
              className={`rounded-lg px-3 py-2 text-sm font-semibold transition ${active(l.href) ? "bg-brand-50 text-brand-800" : "text-slate-700 hover:bg-slate-100"}`}>
              {l.label}
            </Link>
          ))}

          <div className="relative" onMouseEnter={() => setMore(true)} onMouseLeave={() => setMore(false)}>
            <button className="rounded-lg px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100">More ▾</button>
            {more && (
              <div className="absolute right-0 top-full w-56 rounded-xl border border-slate-200 bg-white p-2 shadow-xl">
                {MORE.map((l) => (
                  <Link key={l.href} href={l.href} className="block rounded-lg px-3 py-2 text-sm text-slate-700 hover:bg-slate-50">{l.label}</Link>
                ))}
              </div>
            )}
          </div>
        </nav>

        <form onSubmit={submit} className="ml-auto hidden flex-1 max-w-xs lg:block">
          <label className="sr-only" htmlFor="site-search">Search universities and programmes</label>
          <div className="flex items-center gap-2 rounded-xl border border-slate-300 bg-slate-50 px-3 py-2 focus-within:border-brand-500 focus-within:bg-white">
            <span className="text-slate-400" aria-hidden>⌕</span>
            <input id="site-search" value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="University, programme, subject…"
              className="w-full bg-transparent text-sm outline-none placeholder:text-slate-400" />
          </div>
        </form>

        <div className="ml-auto flex items-center gap-2 lg:ml-0">
          <Link href="/profile-assessment"
            onClick={() => track("cta_click", { cta: "free_assessment", placement: "header" }, "Lead")}
            className="hidden rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-brand-700 sm:block">
            Free assessment
          </Link>
          {settings.whatsapp_enabled !== false && settings.whatsapp_number && (
            <a href={whatsappHref(settings.whatsapp_number, settings.whatsapp_default_message)}
              target="_blank" rel="noopener noreferrer"
              onClick={() => track("whatsapp_click", { placement: "header" }, "Contact")}
              className="hidden rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-bold text-white transition hover:bg-emerald-500 sm:block">
              WhatsApp
            </a>
          )}
          <button type="button" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
            className="grid h-10 w-10 place-items-center rounded-xl border border-slate-300 text-slate-700 xl:hidden">
            {open ? "✕" : "☰"}
          </button>
        </div>
      </div>

      {open && (
        <div className="border-t border-slate-200 bg-white xl:hidden">
          <div className="mx-auto max-w-7xl space-y-3 px-4 py-4 sm:px-6">
            <form onSubmit={submit}>
              <div className="flex items-center gap-2 rounded-xl border border-slate-300 bg-slate-50 px-3 py-2.5">
                <span className="text-slate-400" aria-hidden>⌕</span>
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search universities, programmes…"
                  className="w-full bg-transparent text-sm outline-none" />
              </div>
            </form>
            <div className="grid grid-cols-2 gap-1.5">
              {[...PRIMARY, ...MORE].map((l) => (
                <Link key={l.href} href={l.href}
                  className={`rounded-lg px-3 py-2.5 text-sm font-semibold ${active(l.href) ? "bg-brand-50 text-brand-800" : "bg-slate-50 text-slate-700"}`}>
                  {l.label}
                </Link>
              ))}
            </div>
            <Link href="/profile-assessment" className="block rounded-xl bg-brand-800 px-4 py-3 text-center text-sm font-bold text-white">
              Free profile assessment
            </Link>
          </div>
        </div>
      )}
    </header>
  );
}
