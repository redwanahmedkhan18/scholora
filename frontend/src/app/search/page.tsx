import type { Metadata } from "next";
import Link from "next/link";
import { apiOrNull } from "@/lib/api";
import { LEVEL_LABEL, money, universityHref } from "@/lib/format";
import { Empty, PageHeader } from "@/components/ui";

export const revalidate = 60;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Search",
  description: "Search universities, programmes, destinations, scholarships and faculty across the Scholara database.",
  robots: { index: false, follow: true },
};

export default async function SearchPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const q = (Array.isArray(sp.q) ? sp.q[0] : sp.q) ?? "";
  const data = q ? await apiOrNull<Dict>(`/search?q=${encodeURIComponent(q)}&limit=8`, 60) : null;

  const groups: { key: string; title: string; href: string; items: Dict[]; render: (x: Dict) => React.ReactNode }[] = [
    {
      key: "universities", title: "Universities", href: `/universities?q=${encodeURIComponent(q)}`,
      items: data?.universities ?? [],
      render: (u) => (
        <Link href={universityHref(u)} className="block rounded-lg px-3 py-2.5 hover:bg-slate-50">
          <p className="text-sm font-semibold text-slate-900">{u.name}</p>
          <p className="text-[11px] text-slate-500">{u.country?.flag_emoji} {u.city} · {u.program_count ?? 0} programmes</p>
        </Link>
      ),
    },
    {
      key: "programs", title: "Programmes", href: `/programs?q=${encodeURIComponent(q)}`,
      items: data?.programs ?? [],
      render: (p) => (
        <Link href={`/programs/${p.slug}`} className="block rounded-lg px-3 py-2.5 hover:bg-slate-50">
          <p className="text-sm font-semibold text-slate-900">{p.name}</p>
          <p className="text-[11px] text-slate-500">
            {LEVEL_LABEL[p.degree_level] ?? p.degree_level} · {p.university?.name} · {money(p.tuition_annual, p.currency)}/yr
          </p>
        </Link>
      ),
    },
    {
      key: "countries", title: "Destinations", href: "/study-destinations",
      items: data?.countries ?? [],
      render: (c) => (
        <Link href={`/study-destinations/${c.slug}`} className="block rounded-lg px-3 py-2.5 hover:bg-slate-50">
          <p className="text-sm font-semibold text-slate-900">{c.flag_emoji} {c.name}</p>
          <p className="text-[11px] text-slate-500">{c.tagline}</p>
        </Link>
      ),
    },
    {
      key: "scholarships", title: "Scholarships", href: `/scholarships?q=${encodeURIComponent(q)}`,
      items: data?.scholarships ?? [],
      render: (s) => (
        <Link href={`/scholarships/${s.slug}`} className="block rounded-lg px-3 py-2.5 hover:bg-slate-50">
          <p className="text-sm font-semibold text-slate-900">{s.name}</p>
          <p className="text-[11px] text-slate-500">{s.provider}</p>
        </Link>
      ),
    },
    {
      key: "faculty", title: "Faculty", href: "/phd",
      items: data?.faculty ?? [],
      render: (f) => (
        <Link href={`/faculty/${f.slug}`} className="block rounded-lg px-3 py-2.5 hover:bg-slate-50">
          <p className="text-sm font-semibold text-slate-900">{f.full_name}</p>
          <p className="text-[11px] text-slate-500">{f.title} · {f.university?.name}</p>
        </Link>
      ),
    },
  ];

  const total = groups.reduce((n, g) => n + g.items.length, 0);

  return (
    <>
      <PageHeader eyebrow="Search" title={q ? `Results for “${q}”` : "Search everything"}
        description={q ? `${total} result${total === 1 ? "" : "s"} across universities, programmes, destinations, scholarships and faculty.` : "Use the search box in the header, or jump straight to the filtered search pages."} />

      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        {!q && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[["/universities", "University search"], ["/programs", "Programme search"],
              ["/scholarships", "Scholarship search"], ["/phd", "Supervisor search"]].map(([h, l]) => (
              <Link key={h} href={h} className="card p-5 text-sm font-bold text-brand-800">{l} →</Link>
            ))}
          </div>
        )}

        {q && total === 0 && (
          <Empty title="Nothing matched that search"
            hint="Try a shorter keyword, or browse by destination. If a university is missing it simply means we have not source-verified it yet." />
        )}

        {q && total > 0 && (
          <div className="grid gap-4 lg:grid-cols-2">
            {groups.filter((g) => g.items.length > 0).map((g) => (
              <section key={g.key} className="card p-4">
                <div className="mb-2 flex items-center justify-between">
                  <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">{g.title}</h2>
                  <Link href={g.href} className="text-[11px] font-semibold text-brand-800">Refine →</Link>
                </div>
                <div className="divide-y divide-slate-100">
                  {g.items.map((x, i) => <div key={x.id ?? i}>{g.render(x)}</div>)}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
