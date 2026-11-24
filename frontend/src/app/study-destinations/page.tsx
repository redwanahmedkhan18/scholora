import type { Metadata } from "next";
import Link from "next/link";
import { api } from "@/lib/api";
import { range } from "@/lib/format";
import { CountryCard, Grid, PageHeader } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Study destinations for Bangladeshi students",
  description:
    "Compare study destinations side by side: tuition and living cost ranges, intakes, popular subjects, work rights and the universities we currently hold verified data for.",
  alternates: { canonical: "/study-destinations" },
};

export default async function DestinationsPage() {
  const countries = await api<Dict[]>("/countries", 300);
  const active = countries.filter((c) => c.status !== "COMING_SOON");
  const soon = countries.filter((c) => c.status === "COMING_SOON");

  return (
    <>
      <PageHeader
        eyebrow="Destinations"
        title="Where can you study?"
        description="Every destination below is backed by university, programme, scholarship and intake records held in our database — not a brochure list. Figures are annual ranges in the destination currency and are confirmed per programme before you apply."
      />

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 sm:py-14">
        <Grid cols={4}>
          {active.map((c) => <CountryCard key={c.id} c={c} />)}
        </Grid>

        <h2 className="mb-4 mt-12 text-xl font-bold text-slate-900">Compare at a glance</h2>
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 font-semibold">Destination</th>
                <th className="px-4 py-3 font-semibold">Tuition / year</th>
                <th className="px-4 py-3 font-semibold">Living / year</th>
                <th className="px-4 py-3 font-semibold">Intakes</th>
                <th className="px-4 py-3 font-semibold">Popular subjects</th>
                <th className="px-4 py-3 font-semibold">Records</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {active.map((c) => (
                <tr key={c.id} className="align-top hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link href={`/study-destinations/${c.slug}`} className="font-semibold text-brand-800">
                      {c.flag_emoji} {c.name}
                    </Link>
                    <p className="mt-0.5 text-[11px] text-slate-500">{c.region}</p>
                  </td>
                  <td className="px-4 py-3 text-slate-700">{range(c.tuition_min, c.tuition_max, c.currency)}</td>
                  <td className="px-4 py-3 text-slate-700">{range(c.living_cost_min, c.living_cost_max, c.currency)}</td>
                  <td className="px-4 py-3 text-slate-700">{(c.intakes ?? []).join(", ") || "—"}</td>
                  <td className="px-4 py-3 text-slate-700">{(c.popular_subjects ?? []).slice(0, 3).join(", ") || "—"}</td>
                  <td className="px-4 py-3 text-[11px] text-slate-500">
                    {c.counts?.universities ?? 0} universities<br />{c.counts?.programs ?? 0} programmes
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Ranges are indicative and vary by university, city and programme. The exact figure for a programme is shown on
          its own page with the source and verification date.
        </p>

        {soon.length > 0 && (
          <>
            <h2 className="mb-4 mt-12 text-xl font-bold text-slate-900">Opening soon</h2>
            <Grid cols={4}>{soon.map((c) => <CountryCard key={c.id} c={c} />)}</Grid>
          </>
        )}
      </div>
    </>
  );
}
