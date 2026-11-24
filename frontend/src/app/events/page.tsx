import type { Metadata } from "next";
import { api, getOptions, getSettings } from "@/lib/api";
import { dateTimeLabel } from "@/lib/format";
import { EventRegister } from "@/components/forms";
import { Empty, PageHeader } from "@/components/ui";

export const revalidate = 120;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Events, seminars and admission days",
  description:
    "Free admission days, university delegate visits, scholarship workshops and IELTS sessions at our Uttara office and online. Registration is free and seats are limited.",
  alternates: { canonical: "/events" },
};

export default async function EventsPage() {
  const [events, countries, settings, degreeLevels] = await Promise.all([
    api<Dict[]>("/events?upcoming=true", 120),
    api<Dict[]>("/countries", 300),
    getSettings(),
    getOptions("degree_levels"),
  ]);

  const ld = {
    "@context": "https://schema.org",
    "@graph": events.map((e) => ({
      "@type": "Event", name: e.title, startDate: e.start_at, endDate: e.end_at,
      eventAttendanceMode: e.mode === "ONLINE"
        ? "https://schema.org/OnlineEventAttendanceMode"
        : "https://schema.org/OfflineEventAttendanceMode",
      eventStatus: "https://schema.org/EventScheduled",
      location: e.mode === "ONLINE"
        ? { "@type": "VirtualLocation", url: settings.site_url }
        : { "@type": "Place", name: e.venue, address: e.venue },
      description: e.description,
      organizer: { "@type": "Organization", name: settings.brand_name, url: settings.site_url },
      offers: { "@type": "Offer", price: 0, priceCurrency: "BDT", availability: "https://schema.org/InStock" },
    })),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld) }} />
      <PageHeader
        eyebrow="Events"
        title="Meet us — and meet the universities"
        description="Admission days, delegate visits and workshops at our Uttara office and online. Every event is free. Bring your transcripts and passport copy for an on-the-spot profile assessment."
      />

      <div className="mx-auto max-w-5xl space-y-5 px-4 py-10 sm:px-6">
        {events.length === 0 && <Empty title="No events scheduled right now" hint="Follow our Facebook page — new admission days are announced there first." />}

        {events.map((e) => (
          <article key={e.id} id={e.slug} className="card scroll-mt-24 p-6">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap gap-2">
                  <span className="chip bg-brand-800 text-white">{e.mode}</span>
                  <span className="chip bg-slate-100 text-slate-700">{e.status}</span>
                  {e.capacity && <span className="chip bg-accent-500/15 text-accent-600">{e.capacity} seats</span>}
                </div>
                <h2 className="mt-3 text-xl font-bold text-slate-900">{e.title}</h2>
                <p className="mt-1 text-sm text-slate-600">{dateTimeLabel(e.start_at)} — {dateTimeLabel(e.end_at)}</p>
                <p className="text-sm text-slate-600">{e.venue}</p>
                {e.speaker && <p className="mt-1 text-xs text-slate-500">With {e.speaker}</p>}
                {e.description && <p className="mt-3 max-w-2xl text-sm leading-relaxed text-slate-700">{e.description}</p>}
              </div>
              <EventRegister event={e} countries={countries} degreeLevels={degreeLevels} />
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
