import type { Metadata } from "next";
import { api, qs } from "@/lib/api";
import FilterBar, { Pager } from "@/components/filter-bar";
import { Disclaimer, Empty, Grid, PageHeader, ScholarshipCard } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Scholarships for Bangladeshi students",
  description:
    "Browse university and government scholarships with their published eligibility criteria, award value, deadline and official source. We state what the criteria say — never a promise of funding.",
  alternates: { canonical: "/scholarships" },
};

export default async function ScholarshipsPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k]![0] : (sp[k] as string | undefined)) ?? "";
  const page = Number(one("page") || 1);
  const query = { q: one("q"), country: one("country"), university: one("university"), degree_level: one("degree_level"), page, page_size: 16 };

  const [data, countries] = await Promise.all([
    api<Dict>(`/scholarships${qs(query)}`, 300),
    api<Dict[]>("/countries", 300),
  ]);

  const baseQuery = new URLSearchParams(
    Object.entries(query).filter(([k, v]) => v && k !== "page" && k !== "page_size").map(([k, v]) => [k, String(v)]),
  ).toString();

  return (
    <>
      <PageHeader
        eyebrow="Funding"
        title="Scholarships and funded places"
        description="Each entry shows the published eligibility criteria, the award value, the stated deadline and a link to the official page. If you match the criteria we say the stated criteria appear to be met — nothing more."
      />

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <FilterBar
          fields={[
            { kind: "text", name: "q", label: "Name or provider", placeholder: "e.g. excellence" },
            {
              kind: "select", name: "country", label: "Destination",
              options: [{ value: "", label: "Any destination" },
                ...countries.filter((c) => c.status !== "COMING_SOON").map((c) => ({ value: c.slug, label: `${c.flag_emoji} ${c.name}` }))],
            },
            {
              kind: "select", name: "degree_level", label: "Degree level",
              options: [{ value: "", label: "Any level" }, { value: "BACHELOR", label: "Bachelor" },
                { value: "MASTER", label: "Master" }, { value: "PHD", label: "PhD" }],
            },
          ]}
        />

        <p className="mb-4 mt-6 text-sm text-slate-600">
          <strong className="text-slate-900">{data.total}</strong> scholarship{data.total === 1 ? "" : "s"} recorded
        </p>

        {data.items?.length ? (
          <Grid cols={4}>{data.items.map((s: Dict) => <ScholarshipCard key={s.id} s={s} />)}</Grid>
        ) : (
          <Empty title="No scholarships match those filters" hint="Clear a filter or ask a counselor about funding that is not yet published here." />
        )}

        <Pager total={data.total} page={data.page} pageSize={data.page_size} baseQuery={baseQuery} />

        <div className="mt-8"><Disclaimer>
          Scholarship criteria, values and deadlines change every cycle. We publish what the official page said on the
          verification date shown. Matching the stated criteria does not guarantee an award — most scholarships are
          competitive and discretionary.
        </Disclaimer></div>
      </div>
    </>
  );
}
