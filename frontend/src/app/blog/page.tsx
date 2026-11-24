import type { Metadata } from "next";
import { api, qs } from "@/lib/api";
import FilterBar, { Pager } from "@/components/filter-bar";
import { Empty, Grid, PageHeader, PostCard } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Study abroad guides and news",
  description:
    "Application timelines, English test strategy, scholarship guides, visa financial documentation and intake planning — written for Bangladeshi students by the Scholara counseling team.",
  alternates: { canonical: "/blog" },
};

export default async function BlogPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k]![0] : (sp[k] as string | undefined)) ?? "";
  const page = Number(one("page") || 1);
  const query = { q: one("q"), category: one("category"), tag: one("tag"), page, page_size: 12 };
  const data = await api<Dict>(`/blog${qs(query)}`, 300);

  const categories = Array.from(new Set((data.items ?? []).map((p: Dict) => p.category))).filter(Boolean) as string[];
  const baseQuery = new URLSearchParams(
    Object.entries(query).filter(([k, v]) => v && k !== "page" && k !== "page_size").map(([k, v]) => [k, String(v)]),
  ).toString();

  return (
    <>
      <PageHeader eyebrow="Blog" title="Guides written from real applications"
        description="No listicles. Each article comes from problems our counselors actually solve: deadlines, document rejections, funding gaps and test planning." />
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
        <FilterBar fields={[
          { kind: "text", name: "q", label: "Search", placeholder: "visa, IELTS, scholarship" },
          { kind: "select", name: "category", label: "Category", options: [{ value: "", label: "All categories" }, ...categories.map((c) => ({ value: c, label: c }))] },
        ]} />
        <p className="mb-4 mt-6 text-sm text-slate-600"><strong className="text-slate-900">{data.total}</strong> article{data.total === 1 ? "" : "s"}</p>
        {data.items?.length
          ? <Grid cols={3}>{data.items.map((p: Dict) => <PostCard key={p.id} p={p} />)}</Grid>
          : <Empty title="No articles match" hint="Try a different keyword." />}
        <Pager total={data.total} page={data.page} pageSize={data.page_size} baseQuery={baseQuery} />
      </div>
    </>
  );
}
