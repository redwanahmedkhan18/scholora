import type { Metadata } from "next";
import { api, qs } from "@/lib/api";
import FilterBar, { Pager } from "@/components/filter-bar";
import { Empty, Grid, PageHeader, UniversityCard } from "@/components/ui";

export const revalidate = 120;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "University search",
  description:
    "Search universities by country, city, degree level, subject, tuition, ranking, intake, IELTS and CGPA. Every record links to the official university source with a verification date.",
  alternates: { canonical: "/universities" },
};

export default async function UniversitiesPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k]![0] : (sp[k] as string | undefined)) ?? "";
  const page = Number(one("page") || 1);

  const query = {
    q: one("q"), country: one("country"), city: one("city"), degree_level: one("degree_level"),
    subject: one("subject"), max_tuition: one("max_tuition"), intake: one("intake"),
    max_ielts: one("max_ielts"), max_cgpa: one("max_cgpa"), scholarship: one("scholarship"),
    sort: one("sort"), page, page_size: 12,
  };

  const [data, countries] = await Promise.all([
    api<Dict>(`/universities${qs(query)}`, 120),
    api<Dict[]>("/countries", 300),
  ]);

  const baseQuery = new URLSearchParams(
    Object.entries(query).filter(([k, v]) => v && k !== "page" && k !== "page_size").map(([k, v]) => [k, String(v)]),
  ).toString();

  return (
    <>
      <PageHeader
        eyebrow="University search"
        title="Search universities"
        description="Filter by destination, degree level, subject, budget, intake and entry requirements. Results are built from programme-level records, so a university appears when at least one of its programmes matches your filters."
      />

      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <FilterBar
          fields={[
            { kind: "text", name: "q", label: "Name or city", placeholder: "e.g. Windsor" },
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
            { kind: "text", name: "subject", label: "Subject", placeholder: "computer science" },
            { kind: "number", name: "max_tuition", label: "Max tuition / year", placeholder: "30000" },
            { kind: "text", name: "intake", label: "Intake", placeholder: "September 2027" },
            { kind: "number", name: "max_ielts", label: "My IELTS (max required)", placeholder: "6.5" },
            { kind: "number", name: "max_cgpa", label: "My CGPA (max required)", placeholder: "3.0" },
            { kind: "toggle", name: "scholarship", label: "Scholarship available" },
          ]}
          sorts={[
            { value: "", label: "Relevance" },
            { value: "name", label: "Name A–Z" },
            { value: "ranking", label: "Ranking" },
            { value: "tuition_asc", label: "Lowest tuition" },
            { value: "tuition_desc", label: "Highest tuition" },
          ]}
        />

        <p className="mb-4 mt-6 text-sm text-slate-600">
          <strong className="text-slate-900">{data.total}</strong> universit{data.total === 1 ? "y" : "ies"} match your filters
          {data.total > 0 && ` · page ${data.page} of ${Math.max(1, Math.ceil(data.total / data.page_size))}`}
        </p>

        {data.items?.length ? (
          <Grid cols={3}>{data.items.map((u: Dict) => <UniversityCard key={u.id} u={u} />)}</Grid>
        ) : (
          <Empty title="No universities match those filters yet"
            hint="Try widening the budget, removing the subject filter, or clearing the intake. We only publish institutions we hold source-verified records for." />
        )}

        <Pager total={data.total} page={data.page} pageSize={data.page_size} baseQuery={baseQuery} />
      </div>
    </>
  );
}
