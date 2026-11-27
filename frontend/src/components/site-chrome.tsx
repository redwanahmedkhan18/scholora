"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getProfileToken, readLocalShortlist, writeLocalShortlist } from "@/lib/portal";
import { track } from "@/lib/track";
import { contactFromSettings, mailtoHref, safeExternalHref, telHref, whatsappHref } from "@/lib/contact";

type Dict = Record<string, any>;

/* ------------------------------------------------------- floating WhatsApp */
export function WhatsAppButton({ settings }: { settings: Dict }) {
  const pathname = usePathname();
  const [show, setShow] = useState(false);
  useEffect(() => { const t = setTimeout(() => setShow(true), 900); return () => clearTimeout(t); }, []);
  if (settings.whatsapp_enabled === false || !settings.whatsapp_number) return null;
  if (pathname.startsWith("/admin") || pathname.startsWith("/brochure")) return null;
  const contact = contactFromSettings(settings);
  if (!contact.whatsappHref) return null;

  return (
    <a
      href={whatsappHref(settings.whatsapp_number, contact.whatsappMessage, `(page: ${pathname})`) ?? undefined}
      target="_blank" rel="noopener noreferrer"
      onClick={() => track("whatsapp_click", { placement: "floating", path: pathname }, "Contact")}
      aria-label="Chat with a counselor on WhatsApp"
      className={`no-print fixed bottom-20 right-4 z-40 flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-3 text-sm font-bold text-white shadow-xl shadow-emerald-900/25 transition-all hover:bg-emerald-500 md:bottom-6 ${show ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"}`}
    >
      <svg viewBox="0 0 24 24" className="h-5 w-5 fill-current" aria-hidden>
        <path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.46 1.32 4.96L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.91-4.45 9.91-9.91C21.96 6.45 17.5 2 12.04 2m0 18.15h-.01a8.2 8.2 0 0 1-4.19-1.15l-.3-.18-3.12.82.83-3.04-.2-.31a8.22 8.22 0 0 1-1.26-4.38c0-4.54 3.7-8.23 8.25-8.23a8.23 8.23 0 0 1 8.24 8.24c0 4.54-3.7 8.23-8.24 8.23m4.52-6.16c-.25-.12-1.47-.72-1.69-.81s-.39-.12-.56.13-.64.8-.79.97-.29.19-.54.06a6.7 6.7 0 0 1-1.99-1.23 7.4 7.4 0 0 1-1.37-1.71c-.14-.25-.02-.38.11-.5s.25-.29.37-.44.17-.25.25-.41a.46.46 0 0 0-.02-.44c-.06-.12-.56-1.35-.76-1.85s-.41-.42-.56-.43h-.48a.92.92 0 0 0-.67.31 2.8 2.8 0 0 0-.87 2.08c0 1.23.89 2.41 1.02 2.58s1.76 2.68 4.26 3.76c.6.26 1.06.41 1.42.52.6.19 1.14.16 1.57.1.48-.07 1.47-.6 1.68-1.18s.21-1.08.14-1.18-.22-.17-.47-.29" />
      </svg>
      <span className="hidden sm:inline">Chat on WhatsApp</span>
    </a>
  );
}

/* --------------------------------------------------- mobile bottom nav bar */
export function BottomNav({ settings }: { settings: Dict }) {
  const pathname = usePathname();
  const [count, setCount] = useState(0);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const read = async () => {
      const items = await readLocalShortlist();
      if (!active) return;
      setCount(items.length);
      setToken(getProfileToken());
    };
    void read();
    window.addEventListener("scholara:shortlist", read);
    window.addEventListener("scholara:profile", read);
    window.addEventListener("storage", read);
    return () => {
      active = false;
      window.removeEventListener("scholara:shortlist", read);
      window.removeEventListener("scholara:profile", read);
      window.removeEventListener("storage", read);
    };
  }, [pathname]);

  if (pathname.startsWith("/admin") || pathname.startsWith("/brochure")) return null;

  const items = [
    { href: "/", label: "Home", icon: "⌂" },
    { href: "/search", label: "Search", icon: "⌕" },
    { href: "/shortlist", label: "Shortlist", icon: "★", badge: count },
    { href: "wa", label: "WhatsApp", icon: "✆" },
    { href: token ? `/my/${token}` : "/profile-assessment", label: token ? "My portal" : "Profile", icon: "☺" },
  ];

  return (
    <nav className="no-print fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 backdrop-blur md:hidden" aria-label="Primary mobile navigation">
      <ul className="mx-auto grid max-w-lg grid-cols-5">
        {items.map((it) => {
          const isActive = it.href !== "wa" && (it.href === "/" ? pathname === "/" : pathname.startsWith(it.href));
          const inner = (
            <>
              <span className="relative text-lg leading-none" aria-hidden>
                {it.icon}
                {!!it.badge && (
                  <span className="absolute -right-2.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent-500 px-1 text-[9px] font-bold text-brand-950">
                    {it.badge}
                  </span>
                )}
              </span>
              <span className="mt-0.5 text-[10px] font-semibold">{it.label}</span>
            </>
          );
          return (
            <li key={it.label}>
              {it.href === "wa" ? (
                <a href={whatsappHref(settings.whatsapp_number, settings.whatsapp_default_message) ?? undefined}
                  target="_blank" rel="noopener noreferrer"
                  onClick={() => track("whatsapp_click", { placement: "bottom_nav" }, "Contact")}
                  className="flex flex-col items-center py-2 text-emerald-600">
                  {inner}
                </a>
              ) : (
                <Link href={it.href} className={`flex flex-col items-center py-2 ${isActive ? "text-brand-800" : "text-slate-500"}`}>
                  {inner}
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/* ------------------------------------------------------- shortlist toggles */
export function ShortlistButton({ item, className = "" }: { item: Dict; className?: string }) {
  const [on, setOn] = useState(false);

  useEffect(() => {
    let active = true;
    const load = () => {
      void readLocalShortlist().then((list) => {
        if (active) setOn(list.some((x: Dict) => x.slug === item.slug));
      });
    };
    load();
    window.addEventListener("scholara:profile", load);
    window.addEventListener("storage", load);
    return () => {
      active = false;
      window.removeEventListener("scholara:profile", load);
      window.removeEventListener("storage", load);
    };
  }, [item.slug]);

  const toggle = async () => {
    const identity = getProfileToken();
    const list = await readLocalShortlist(identity);
    if (getProfileToken() !== identity) return;
    const exists = list.some((x) => x.slug === item.slug);
    const next = exists ? list.filter((x) => x.slug !== item.slug) : [...list, item];
    if (!(await writeLocalShortlist(next, identity)) || getProfileToken() !== identity) return;
    setOn(!exists);
    window.dispatchEvent(new Event("scholara:shortlist"));
    track(exists ? "shortlist_remove" : "shortlist_add", { slug: item.slug, name: item.name }, exists ? undefined : "AddToWishlist");
  };

  return (
    <button type="button" onClick={toggle}
      className={`rounded-xl px-4 py-2.5 text-sm font-bold ring-1 transition ${on ? "bg-accent-500 text-brand-950 ring-accent-500" : "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50"} ${className}`}>
      {on ? "★ Saved to shortlist" : "☆ Add to shortlist"}
    </button>
  );
}

/* ------------------------------------------------------------------ footer */
export function SiteFooter({ settings, countries }: { settings: Dict; countries: Dict[] }) {
  const pathname = usePathname();
  if (pathname.startsWith("/admin") || pathname.startsWith("/brochure")) return null;

  const year = new Date().getFullYear();
  return (
    <footer className="no-print mt-auto bg-brand-950 pb-24 pt-14 text-brand-100 md:pb-14">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-[1.4fr_1fr_1fr_1.2fr]">
          <div>
            <div className="flex items-center gap-2">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-white text-base font-black text-brand-900">S</span>
              <div>
                <p className="text-lg font-black text-white">{settings.brand_name ?? "Scholara"}</p>
                <p className="text-[11px] uppercase tracking-wider text-accent-400">{settings.tagline}</p>
              </div>
            </div>
            <p className="mt-4 max-w-sm text-sm leading-relaxed">{settings.about_short}</p>
            <address className="mt-4 space-y-1 text-sm not-italic">
              <p>{settings.office_address}</p>
              <p>{settings.office_hours}</p>
              {Array.isArray(settings.phones) && settings.phones.filter((p: string) => telHref(p)).map((p: string) => (
                <p key={p}><a href={telHref(p) ?? undefined} className="hover:text-accent-400">{p}</a></p>
              ))}
              {Array.isArray(settings.emails) && settings.emails.filter((e: string) => mailtoHref(e)).map((e: string) => (
                <p key={e}><a href={mailtoHref(e) ?? undefined} className="hover:text-accent-400">{e}</a></p>
              ))}
            </address>
          </div>

          <nav aria-label="Destinations">
            <h3 className="text-xs font-bold uppercase tracking-[0.16em] text-white">Destinations</h3>
            <ul className="mt-4 space-y-2 text-sm">
              {countries.filter((c) => c.status !== "COMING_SOON").slice(0, 9).map((c) => (
                <li key={c.id}>
                  <Link href={`/study-destinations/${c.slug}`} className="hover:text-accent-400">
                    {c.flag_emoji} Study in {c.name}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <nav aria-label="Explore">
            <h3 className="text-xs font-bold uppercase tracking-[0.16em] text-white">Explore</h3>
            <ul className="mt-4 space-y-2 text-sm">
              {[["/universities", "University search"], ["/programs", "Programme search"], ["/scholarships", "Scholarships"],
                ["/phd", "PhD & supervisors"], ["/services", "Our services"], ["/success-stories", "Success stories"],
                ["/events", "Events"], ["/blog", "Blog"], ["/faqs", "FAQs"], ["/contact", "Contact us"]].map(([h, l]) => (
                <li key={h}><Link href={h} className="hover:text-accent-400">{l}</Link></li>
              ))}
            </ul>
          </nav>

          <div>
            <h3 className="text-xs font-bold uppercase tracking-[0.16em] text-white">Talk to us</h3>
            <div className="mt-4 space-y-2">
              <Link href="/profile-assessment" className="block rounded-xl bg-accent-500 px-4 py-2.5 text-center text-sm font-bold text-brand-950 hover:bg-accent-400">
                Free profile assessment
              </Link>
              <Link href="/appointments" className="block rounded-xl bg-white/10 px-4 py-2.5 text-center text-sm font-bold text-white ring-1 ring-white/20 hover:bg-white/20">
                Book a counselor
              </Link>
              {settings.whatsapp_number && whatsappHref(settings.whatsapp_number, settings.whatsapp_default_message) && (
                <a href={whatsappHref(settings.whatsapp_number, settings.whatsapp_default_message) ?? undefined} target="_blank" rel="noopener noreferrer"
                  onClick={() => track("whatsapp_click", { placement: "footer" }, "Contact")}
                  className="block rounded-xl bg-emerald-600 px-4 py-2.5 text-center text-sm font-bold text-white hover:bg-emerald-500">
                  WhatsApp {settings.whatsapp_number}
                </a>
              )}
            </div>

            <h3 className="mt-6 text-xs font-bold uppercase tracking-[0.16em] text-white">Follow</h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {[["Facebook", settings.facebook_page_url], ["Messenger", settings.messenger_url],
                ["Instagram", settings.instagram_url], ["LinkedIn", settings.linkedin_url],
                ["YouTube", settings.youtube_url]]
                .filter(([, href]) => !!safeExternalHref(href))
                .map(([label, href]) => (
                  <a key={label as string} href={safeExternalHref(href) ?? undefined} target="_blank" rel="noopener noreferrer"
                    onClick={() => track("social_click", { network: label })}
                    className="rounded-lg bg-white/10 px-3 py-1.5 text-xs font-semibold text-white ring-1 ring-white/15 hover:bg-white/20">
                    {label}
                  </a>
                ))}
            </div>
          </div>
        </div>

        <div className="mt-10 border-t border-white/10 pt-6 text-xs leading-relaxed">
          <p className="rounded-lg bg-white/5 p-3">
            <strong className="text-white">Data integrity notice.</strong> University, programme, fee, intake,
            scholarship and faculty records on this site are published with their source, source type and verification
            date. Where sources disagree we show the conflict rather than silently picking one. Eligibility results are
            stated as <em>meets / partially meets / does not meet / requires review / information unavailable</em> —
            we never publish an invented admission probability, and no result here is an offer, scholarship or visa
            guarantee.
          </p>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <p>© {year} {settings.brand_name ?? "Scholara"}. All rights reserved.</p>
            <p className="flex flex-wrap gap-4">
              <Link href="/faqs" className="hover:text-accent-400">FAQs</Link>
              <Link href="/contact" className="hover:text-accent-400">Contact</Link>
              <Link href="/admin" className="hover:text-accent-400">Staff login</Link>
            </p>
          </div>
        </div>
      </div>
    </footer>
  );
}

/* ----------------------------------------------- Facebook page embed block */
export function FacebookPanel({ settings }: { settings: Dict }) {
  const pageHref = safeExternalHref(settings.facebook_page_url);
  const messengerHref = safeExternalHref(settings.messenger_url);
  if (!pageHref) return null;
  return (
    <div className="card p-5">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#1877F2] text-lg font-black text-white" aria-hidden>f</span>
        <div>
          <p className="text-sm font-bold text-slate-900">{settings.brand_name} on Facebook</p>
          <p className="text-[11px] text-slate-500">Daily intake alerts, scholarship deadlines and seminar invites.</p>
        </div>
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        <a href={pageHref} target="_blank" rel="noopener noreferrer"
          onClick={() => track("facebook_page_click", { placement: "panel" })}
          className="rounded-xl bg-[#1877F2] px-4 py-2.5 text-center text-sm font-bold text-white hover:brightness-110">
          Visit our page
        </a>
        {messengerHref && (
          <a href={messengerHref} target="_blank" rel="noopener noreferrer"
            onClick={() => track("messenger_click", { placement: "panel" }, "Contact")}
            className="rounded-xl bg-slate-900 px-4 py-2.5 text-center text-sm font-bold text-white hover:bg-slate-800">
            Message us
          </a>
        )}
      </div>
      <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
        The live Facebook page plugin is loaded on the production domain once the page is connected in
        Admin → Marketing. In this preview we link out instead, so no third-party script runs.
      </p>
    </div>
  );
}

/* ------------------------------------------- start an application (portal) */
export function StartApplicationButton({ programSlug, intakes = [] }:
  { programSlug: string; intakes?: string[] }) {
  const [token, setToken] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const read = () => setToken(getProfileToken());
    read();
    window.addEventListener("scholara:profile", read);
    return () => window.removeEventListener("scholara:profile", read);
  }, []);

  // Without a profile there is nothing to attach an application to — send them to
  // the assessment, which creates the profile and the portal in one step.
  if (!token) {
    return (
      <Link href={`/profile-assessment?program=${programSlug}`}
        className="block rounded-xl bg-white/10 px-4 py-3 text-center text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20">
        Start an application
      </Link>
    );
  }

  if (state === "done") {
    return (
      <Link href={`/my/${token}`}
        className="block rounded-xl bg-emerald-600 px-4 py-3 text-center text-sm font-bold text-white hover:bg-emerald-500">
        Added — open my portal →
      </Link>
    );
  }

  const start = async () => {
    setState("busy");
    try {
      const res = await fetch(`/api/v1/portal/${token}/applications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ program_slug: programSlug, intake_label: intakes[0] ?? null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.detail ?? "Could not start the application.");
      setState("done");
      setMessage(data.duplicate ? "This programme was already on your list." : "");
      track("application_start", { slug: programSlug }, "SubmitApplication");
    } catch (e: any) {
      setState("error");
      setMessage(e?.message ?? "Something went wrong.");
    }
  };

  return (
    <div>
      <button type="button" onClick={start} disabled={state === "busy"}
        className="w-full rounded-xl bg-white/10 px-4 py-3 text-center text-sm font-bold text-white ring-1 ring-white/25 hover:bg-white/20 disabled:opacity-60">
        {state === "busy" ? "Adding…" : "Start an application"}
      </button>
      {message && <p className="mt-1.5 text-[11px] text-brand-100">{message}</p>}
    </div>
  );
}
