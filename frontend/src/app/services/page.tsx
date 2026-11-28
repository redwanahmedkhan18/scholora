import type { Metadata } from "next";
import Link from "next/link";
import { api } from "@/lib/api";
import { Grid, PageHeader, ServiceCard } from "@/components/ui";
import { jsonLdScript } from "@/lib/json-ld";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Our services",
  description:
    "Career counseling, university selection, admission processing, scholarship guidance, visa support, accommodation, airport pickup, pre-departure briefing and post-landing help for Bangladeshi students.",
  alternates: { canonical: "/services" },
};

export default async function ServicesPage() {
  const services = await api<Dict[]>("/services", 300);
  const grouped = services.reduce<Record<string, Dict[]>>((acc, s) => {
    const k = s.category ?? "Other";
    (acc[k] ??= []).push(s);
    return acc;
  }, {});

  const ld = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    itemListElement: services.map((s, i) => ({
      "@type": "ListItem", position: i + 1, name: s.title, description: s.summary,
    })),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(ld) }} />
      <PageHeader
        eyebrow="Services"
        title="Everything between your transcript and your first lecture"
        description="Each service below is a tracked workflow, not a promise. Your counselor, your documents, your deadlines and your application status all live on one record that you and the office can both see."
      >
        <div className="flex flex-wrap gap-3">
          <Link href="/appointments" className="rounded-xl bg-accent-500 px-5 py-3 text-sm font-bold text-brand-950">Book a free session</Link>
          <Link href="/profile-assessment" className="rounded-xl bg-white/10 px-5 py-3 text-sm font-bold text-white ring-1 ring-white/25">Start with the free assessment</Link>
        </div>
      </PageHeader>

      <div className="mx-auto max-w-7xl space-y-10 px-4 py-10 sm:px-6 sm:py-14">
        {Object.entries(grouped).map(([category, items]) => (
          <section key={category}>
            <h2 className="mb-4 text-xl font-bold text-slate-900">{category}</h2>
            <Grid cols={4}>{items.map((s) => <ServiceCard key={s.id} s={s} />)}</Grid>
          </section>
        ))}
      </div>
    </>
  );
}
