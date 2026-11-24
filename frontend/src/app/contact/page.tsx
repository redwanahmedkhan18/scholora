import type { Metadata } from "next";
import Link from "next/link";
import { api, getOptions, getSettings } from "@/lib/api";
import { LeadForm } from "@/components/forms";
import { FacebookPanel } from "@/components/site-chrome";
import { PageHeader } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export async function generateMetadata(): Promise<Metadata> {
  const s = await getSettings();
  return {
    title: "Contact us",
    description: `Visit ${s.office_address}, call ${s.phones?.[0] ?? ""}, message us on WhatsApp or Messenger, or send an enquiry. ${s.office_hours ?? ""}`,
    alternates: { canonical: "/contact" },
  };
}

export default async function ContactPage() {
  const [settings, countries, degreeLevels] = await Promise.all([
    getSettings(), api<Dict[]>("/countries", 300), getOptions("degree_levels"),
  ]);

  const channels = [
    { icon: "📍", title: "Visit the office", body: settings.office_address, note: settings.office_hours, href: settings.map_embed_url ? "#map" : undefined, cta: "See the map" },
    { icon: "💬", title: "WhatsApp", body: settings.whatsapp_number, note: "Fastest reply — usually within office hours.", href: settings.whatsapp_number ? `https://wa.me/${String(settings.whatsapp_number).replace(/\D/g, "")}` : undefined, cta: "Open WhatsApp" },
    { icon: "📞", title: "Call us", body: (settings.phones ?? []).join(" · "), note: "Saturday to Thursday.", href: settings.phones?.[0] ? `tel:${settings.phones[0]}` : undefined, cta: "Call now" },
    { icon: "✉️", title: "Email", body: (settings.emails ?? []).join(" · "), note: "For documents and formal enquiries.", href: settings.emails?.[0] ? `mailto:${settings.emails[0]}` : undefined, cta: "Send email" },
    { icon: "👍", title: "Facebook page", body: settings.facebook_page_url, note: "Intake alerts and seminar announcements.", href: settings.facebook_page_url, cta: "Open page" },
    { icon: "📨", title: "Messenger", body: settings.messenger_url, note: "If you already follow us on Facebook.", href: settings.messenger_url, cta: "Message us" },
  ].filter((c) => c.body);

  const ld = {
    "@context": "https://schema.org", "@type": "ContactPage",
    mainEntity: {
      "@type": "Organization", name: settings.brand_name, telephone: settings.phones?.[0],
      email: settings.emails?.[0],
      address: { "@type": "PostalAddress", streetAddress: settings.office_address, addressLocality: "Uttara, Dhaka", addressCountry: "BD" },
      sameAs: [settings.facebook_page_url, settings.instagram_url, settings.linkedin_url].filter(Boolean),
    },
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld) }} />
      <PageHeader eyebrow="Contact" title="Talk to a counselor in Uttara"
        description="Pick whichever channel suits you. Whatever you use, your enquiry lands on the same record, so you never have to repeat yourself." />

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {channels.map((c) => (
            <div key={c.title} className="card flex flex-col p-5">
              <span className="text-2xl" aria-hidden>{c.icon}</span>
              <h2 className="mt-3 text-base font-bold text-slate-900">{c.title}</h2>
              <p className="mt-1 break-words text-sm text-slate-700">{c.body}</p>
              {c.note && <p className="mt-1 text-[11px] text-slate-500">{c.note}</p>}
              {c.href && (
                <a href={c.href} target={c.href.startsWith("http") ? "_blank" : undefined} rel="noopener noreferrer"
                  className="mt-auto pt-4 text-sm font-semibold text-brand-800">{c.cta} →</a>
              )}
            </div>
          ))}
        </div>

        <div className="mt-8 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <div className="card p-6">
            <h2 className="text-lg font-bold text-slate-900">Send an enquiry</h2>
            <p className="mt-1 text-sm text-slate-600">
              One working day response. Add your CGPA, English score and budget if you want a useful first answer.
            </p>
            <div className="mt-5">
              <LeadForm degreeLevels={degreeLevels} countries={countries} settings={settings} source="WEBSITE" />
            </div>
          </div>

          <aside className="space-y-6">
            <FacebookPanel settings={settings} />
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Prefer a scheduled session?</h2>
              <p className="mt-2 text-sm text-slate-600">
                Book a specific slot with a named counselor instead of waiting for a callback.
              </p>
              <Link href="/appointments" className="mt-4 block rounded-xl bg-brand-800 px-4 py-2.5 text-center text-sm font-bold text-white">
                Book an appointment
              </Link>
            </div>
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Office hours</h2>
              <p className="mt-2 text-sm text-slate-700">{settings.office_hours}</p>
              <p className="mt-2 text-xs text-slate-500">
                Walk-ins are welcome, but booked sessions are prioritised during admission season.
              </p>
            </div>
          </aside>
        </div>

        {settings.map_embed_url && (
          <div id="map" className="card mt-8 overflow-hidden p-0">
            <iframe src={settings.map_embed_url} title="Office location map" loading="lazy"
              referrerPolicy="no-referrer-when-downgrade" className="h-80 w-full border-0" />
          </div>
        )}
      </div>
    </>
  );
}
