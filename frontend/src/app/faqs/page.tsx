import type { Metadata } from "next";
import Link from "next/link";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui";
import { jsonLdScript } from "@/lib/json-ld";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Frequently asked questions",
  description:
    "Fees, eligibility, IELTS and MOI, scholarships, visa documents, study gaps, processing time and what we can and cannot promise — answered plainly.",
  alternates: { canonical: "/faqs" },
};

export default async function FaqsPage() {
  const faqs = await api<Dict[]>("/faqs", 300);
  const grouped = faqs.reduce<Record<string, Dict[]>>((acc, f) => {
    (acc[f.category ?? "General"] ??= []).push(f);
    return acc;
  }, {});

  const ld = {
    "@context": "https://schema.org", "@type": "FAQPage",
    mainEntity: faqs.map((f) => ({
      "@type": "Question", name: f.question, acceptedAnswer: { "@type": "Answer", text: f.answer },
    })),
  };

  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(ld) }} />
      <PageHeader eyebrow="FAQs" title="Straight answers"
        description="If the honest answer is “it depends on the programme”, we say that — and then show you where to look it up." />

      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
        {Object.entries(grouped).map(([category, items]) => (
          <section key={category} className="mb-8">
            <h2 className="mb-3 text-xl font-bold text-slate-900">{category}</h2>
            <div className="space-y-2">
              {items.map((f) => (
                <details key={f.id} className="card group p-0">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-3 p-5 text-sm font-semibold text-slate-900">
                    {f.question}
                    <span className="shrink-0 text-slate-400 transition group-open:rotate-45" aria-hidden>+</span>
                  </summary>
                  <p className="border-t border-slate-100 p-5 pt-4 text-sm leading-relaxed text-slate-600">{f.answer}</p>
                </details>
              ))}
            </div>
          </section>
        ))}

        <div className="card bg-brand-950 p-6 text-white">
          <h2 className="text-lg font-bold">Still unsure?</h2>
          <p className="mt-2 text-sm leading-relaxed text-brand-100">
            Ask us directly — no charge for the first conversation, and no pressure to sign anything.
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link href="/contact" className="rounded-xl bg-accent-500 px-5 py-2.5 text-sm font-bold text-brand-950">Contact us</Link>
            <Link href="/appointments" className="rounded-xl bg-white/10 px-5 py-2.5 text-sm font-bold text-white ring-1 ring-white/20">Book a counselor</Link>
          </div>
        </div>
      </div>
    </>
  );
}
