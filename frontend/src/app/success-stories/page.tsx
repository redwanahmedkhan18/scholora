import type { Metadata } from "next";
import { api, qs } from "@/lib/api";
import FilterBar, { Pager } from "@/components/filter-bar";
import { Empty, Grid, PageHeader, StoryCard } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Success stories",
  description:
    "Real Bangladeshi students placed at universities in the USA, UK, Canada, Australia, Ireland, Germany, Malaysia, Hungary, Cyprus, the UAE and South Korea — with the programme, intake, scholarship and visa outcome.",
  alternates: { canonical: "/success-stories" },
};

export default async function StoriesPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k]![0] : (sp[k] as string | undefined)) ?? "";
  const page = Number(one("page") || 1);
  const query = { q: one("q"), country: one("country"), degree_level: one("degree_level"), year: one("year"), scholarship: one("scholarship"), page, page_size: 12 };

  const [data, countries] = await Promise.all([
    api<Dict>(`/success-stories${qs(query)}`, 300),
    api<Dict[]>("/countries", 300),
  ]);

  const baseQuery = new URLSearchParams(
    Object.entries(query).filter(([k, v]) => v && k !== "page" && k !== "page_size").map(([k, v]) => [k, String(v)]),
  ).toString();

  const years: number[] = data.facets?.years ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Proof, not promises"
        title="Students we placed"
        description="Every story below is published with the student's written consent and records the exact programme, intake, scholarship and visa outcome. We do not publish numbers we cannot attach to a real file."
      />

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <FilterBar
          fields={[
            { kind: "text", name: "q", label: "Student or university", placeholder: "e.g. Windsor" },
            {
              kind: "select", name: "country", label: "Destination",
              options: [{ value: "", label: "Any destination" },
                ...countries.map((c) => ({ value: c.slug, label: `${c.flag_emoji} ${c.name}` }))],
            },
            {
              kind: "select", name: "degree_level", label: "Degree level",
              options: [{ value: "", label: "Any level" }, { value: "BACHELOR", label: "Bachelor" },
                { value: "MASTER", label: "Master" }, { value: "PHD", label: "PhD" }],
            },
            ...(years.length
              ? [{ kind: "select" as const, name: "year", label: "Year", options: [{ value: "", label: "Any year" }, ...years.map((y) => ({ value: String(y), label: String(y) }))] }]
              : []),
            { kind: "toggle", name: "scholarship", label: "With scholarship" },
          ]}
        />

        <p className="mb-4 mt-6 text-sm text-slate-600"><strong className="text-slate-900">{data.total}</strong> published stor{data.total === 1 ? "y" : "ies"}</p>

        {data.items?.length ? (
          <Grid cols={3}>{data.items.map((s: Dict) => <StoryCard key={s.id} s={s} />)}</Grid>
        ) : (
          <Empty title="No stories match those filters" hint="Try clearing the destination or the year." />
        )}

        <Pager total={data.total} page={data.page} pageSize={data.page_size} baseQuery={baseQuery} />
      </div>
    </>
  );
}
