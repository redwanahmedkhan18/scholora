import type { Metadata } from "next";
import { getSettings } from "@/lib/api";
import { contactFromSettings } from "@/lib/contact";
import { AppointmentForm } from "@/components/forms";
import { PageHeader } from "@/components/ui";

export const revalidate = 300;

export const metadata: Metadata = {
  title: "Book a counseling session",
  description:
    "Reserve a free session at our Uttara office, online or by phone. Profile assessment, university selection, scholarship guidance, visa preparation and PhD supervisor strategy.",
  alternates: { canonical: "/appointments" },
};

export default async function AppointmentsPage() {
  const settings = await getSettings();
  const contact = contactFromSettings(settings);

  return (
    <>
      <PageHeader eyebrow="Appointments" title="Book a counseling session"
        description="Choose a slot and a channel. You will get a confirmation on WhatsApp with the counselor's name and what to bring." />

      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <div className="card p-6">
            <AppointmentForm settings={settings} />
          </div>

          <aside className="space-y-6">
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Bring with you</h2>
              <ul className="mt-3 space-y-2 text-sm text-slate-700">
                {["Academic transcripts and certificates", "Passport (or NID if no passport yet)",
                  "IELTS / TOEFL / PTE score report, if taken", "CV, if you have work experience",
                  "An idea of your annual budget"].map((d) => (
                  <li key={d} className="flex gap-2"><span className="text-brand-700" aria-hidden>▢</span>{d}</li>
                ))}
              </ul>
            </div>
            <div className="card p-6">
              <h2 className="text-base font-bold text-slate-900">Where to find us</h2>
              <p className="mt-2 text-sm text-slate-700">{settings.office_address}</p>
              <p className="mt-1 text-sm text-slate-600">{settings.office_hours}</p>
              {contact.telHref && <p className="mt-2 text-sm"><a href={contact.telHref} className="font-semibold text-brand-800">{contact.phoneDisplay}</a></p>}
            </div>
            <div className="card bg-brand-950 p-6 text-white">
              <h2 className="text-base font-bold">Come prepared</h2>
              <p className="mt-2 text-sm leading-relaxed text-brand-100">
                Run the free online assessment first. You will arrive with a shortlist and an eligibility report, and
                the session becomes a decision instead of an introduction.
              </p>
            </div>
          </aside>
        </div>
      </div>
    </>
  );
}
