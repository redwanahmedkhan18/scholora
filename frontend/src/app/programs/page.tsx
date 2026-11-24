import type { Metadata } from "next";
import { api, qs } from "@/lib/api";
import FilterBar, { Pager } from "@/components/filter-bar";
import { Empty, Grid, PageHeader, ProgramCard } from "@/components/ui";

export const revalidate = 120;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Programme search",
  description:
    "Search Bachelor, Master and PhD programmes by destination, subject, tuition, intake and entry requirements. Requirements are stored per programme, with the official source and verification date.",
  alternates: { canonical: "/programs" },
};

export default async function ProgramsPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k]![0] : (sp[k] as string | undefined)) ?? "";
  const page = Number(one("page") || 1);

  const query = {
    q: one("q"), country: one("country"), degree_level: one("degree_level"), subject: one("subject"),
    university: one("university"), max_tuition: one("max_tuition"), intake: one("intake"),
    scholarship: one("scholarship"), sort: one("sort"), page, page_size: 12,
  };

  const [data, countries] = await Promise.all([
    api<Dict>(`/programs${qs(query)}`, 120),
    api<Dict[]>("/countries", 300),
  ]);

  const baseQuery = new URLSearchParams(
    Object.entries(query).filter(([k, v]) => v && k !== "page" && k !== "page_size").map(([k, v]) => [k, String(v)]),
  ).toString();

  return (
    <>
      <PageHeader
        eyebrow="Programme search"
        title="Find the right programme, not just the right university"
        description="Two programmes in the same university can have different fees, different IELTS scores and different intakes. Everything below is stored and filtered at programme level."
      />

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <FilterBar
          fields={[
            { kind: "text", name: "q", label: "Programme or university", placeholder: "e.g. data science" },
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
            { kind: "text", name: "subject", label: "Subject / discipline", placeholder: "business" },
            { kind: "number", name: "max_tuition", label: "Max tuition / year", placeholder: "25000" },
            { kind: "text", name: "intake", label: "Intake", placeholder: "September 2027" },
            { kind: "toggle", name: "scholarship", label: "Funding listed" },
          ]}
          sorts={[
            { value: "", label: "Relevance" },
            { value: "tuition_asc", label: "Lowest tuition" },
            { value: "tuition_desc", label: "Highest tuition" },
            { value: "name", label: "Name A–Z" },
            { value: "duration", label: "Shortest duration" },
          ]}
        />

        <p className="mb-4 mt-6 text-sm text-slate-600">
          <strong className="text-slate-900">{data.total}</strong> programme{data.total === 1 ? "" : "s"} match your filters
        </p>

        {data.items?.length ? (
          <Grid cols={3}>{data.items.map((p: Dict) => <ProgramCard key={p.id} p={p} />)}</Grid>
        ) : (
          <Empty title="No programmes match those filters"
            hint="Try raising the budget ceiling or clearing the intake. If a programme you expect is missing, it simply means we have not source-verified it yet — ask a counselor and we will add it." />
        )}

        <Pager total={data.total} page={data.page} pageSize={data.page_size} baseQuery={baseQuery} />
      </div>
    </>
  );
}
