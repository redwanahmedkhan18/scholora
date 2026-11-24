"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LEVEL_LABEL, money, months } from "@/lib/format";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

/** Side-by-side programme comparison (§31). Data is fetched through the Next proxy, never localhost. */
export default function CompareView({ initialSlugs }: { initialSlugs: string[] }) {
  const [slugs, setSlugs] = useState<string[]>(initialSlugs);
  const [rows, setRows] = useState<Dict[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (initialSlugs.length === 0) {
      try {
        const saved: Dict[] = JSON.parse(localStorage.getItem("scholara.shortlist") ?? "[]");
        setSlugs(saved.filter((x) => x.type === "program").map((x) => x.slug).slice(0, 4));
      } catch { /* ignore */ }
    }
  }, [initialSlugs]);

  useEffect(() => {
    if (slugs.length === 0) { setRows([]); return; }
    let cancelled = false;
    setLoading(true);
    Promise.all(
      slugs.slice(0, 4).map((s) =>
        fetch(`/api/v1/programs/${s}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ),
    ).then((list) => {
      if (cancelled) return;
      setRows(list.filter(Boolean) as Dict[]);
      setLoading(false);
      track("compare_view", { count: list.filter(Boolean).length });
    });
    return () => { cancelled = true; };
  }, [slugs]);

  const drop = (slug: string) => setSlugs((s) => s.filter((x) => x !== slug));

  if (loading || rows === null) return <p className="text-sm text-slate-500">Loading comparison…</p>;

  if (rows.length === 0) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-semibold text-slate-800">Nothing to compare yet</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
          Add two or more programmes to your shortlist, then come back — or open a comparison link from the shortlist page.
        </p>
        <Link href="/programs" className="mt-5 inline-block rounded-xl bg-brand-800 px-5 py-2.5 text-sm font-bold text-white">Browse programmes</Link>
      </div>
    );
  }

  const field = (label: string, fn: (d: Dict) => React.ReactNode) => (
    <tr key={label} className="align-top">
      <th scope="row" className="sticky left-0 z-10 w-44 bg-slate-50 px-3 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </th>
      {rows.map((d, i) => <td key={i} className="min-w-56 border-l border-slate-100 px-3 py-3 text-sm text-slate-800">{fn(d)}</td>)}
    </tr>
  );

  return (
    <div className="card overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <tbody className="divide-y divide-slate-100">
          {field("Programme", (d) => (
            <div>
              <Link href={`/programs/${d.program.slug}`} className="font-bold text-brand-800">{d.program.name}</Link>
              <button type="button" onClick={() => drop(d.program.slug)} className="mt-1 block text-[11px] text-slate-400 underline">remove</button>
            </div>
          ))}
          {field("University", (d) => <>{d.program.university?.country?.flag_emoji} {d.program.university?.name}</>)}
          {field("City", (d) => d.program.campus ?? d.program.university?.city)}
          {field("Level / award", (d) => `${LEVEL_LABEL[d.program.degree_level] ?? d.program.degree_level} · ${d.program.award ?? "—"}`)}
          {field("Duration", (d) => months(d.program.duration_months))}
          {field("Tuition / year", (d) => <strong>{money(d.program.tuition_annual, d.program.currency)}</strong>)}
          {field("Living cost / year", (d) => money(d.program.living_cost_annual, d.program.currency))}
          {field("All-in annual estimate", (d) => <strong>{money(d.cost_estimate?.estimated_annual_total, d.cost_estimate?.currency)}</strong>)}
          {field("Minimum CGPA", (d) => d.program.requirements?.academic?.min_cgpa ?? "Not published")}
          {field("IELTS overall", (d) => d.program.requirements?.english?.ielts_overall ?? "Not published")}
          {field("MOI accepted", (d) => (d.program.requirements?.english?.moi_accepted ? "Yes" : "Not listed"))}
          {field("GRE / GMAT", (d) => {
            const t = d.program.requirements?.tests ?? {};
            if (t.gre_required) return `GRE required (${t.gre_score ?? "score not published"})`;
            if (t.gmat_required) return `GMAT required (${t.gmat_score ?? "score not published"})`;
            return "Not required";
          })}
          {field("Intakes", (d) => (d.program.intakes ?? []).map((i: Dict) => i.label).join(" · ") || "—")}
          {field("Next deadline", (d) => (d.program.intakes ?? [])[0]?.application_deadline ?? "—")}
          {field("Scholarships listed", (d) => (d.scholarships ?? []).length)}
          {field("Source", (d) => (
            d.program.provenance?.source_url
              ? <a href={d.program.provenance.source_url} target="_blank" rel="noopener noreferrer nofollow" className="text-brand-700 underline underline-offset-2">Official page ↗</a>
              : "Not recorded"
          ))}
          {field("Verified", (d) => d.program.provenance?.verified_at?.slice(0, 10) ?? "—")}
        </tbody>
      </table>
    </div>
  );
}
