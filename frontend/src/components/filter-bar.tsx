"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

export type Field =
  | { kind: "text"; name: string; label: string; placeholder?: string }
  | { kind: "select"; name: string; label: string; options: { value: string; label: string }[] }
  | { kind: "number"; name: string; label: string; placeholder?: string }
  | { kind: "toggle"; name: string; label: string };

/** URL-driven filter bar: every filter is a query parameter, so results stay shareable and indexable. */
export default function FilterBar({ fields, sorts }: { fields: Field[]; sorts?: { value: string; label: string }[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const [open, setOpen] = useState(false);

  const current = useCallback((n: string) => sp.get(n) ?? "", [sp]);

  const push = (updates: Dict) => {
    const next = new URLSearchParams(sp.toString());
    Object.entries(updates).forEach(([k, v]) => {
      if (v === "" || v === null || v === undefined || v === false) next.delete(k);
      else next.set(k, String(v));
    });
    next.delete("page");
    track("filter_change", updates);
    router.push(`${pathname}?${next.toString()}`, { scroll: false });
  };

  const activeCount = fields.filter((f) => current(f.name)).length;

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-3 lg:hidden">
        <button type="button" onClick={() => setOpen((o) => !o)}
          className="rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">
          {open ? "Hide filters" : `Filters${activeCount ? ` (${activeCount})` : ""}`}
        </button>
        {activeCount > 0 && (
          <button type="button" onClick={() => router.push(pathname)} className="text-xs font-semibold text-slate-500 underline">
            Clear all
          </button>
        )}
      </div>

      <div className={`${open ? "mt-4 grid" : "hidden"} gap-3 lg:mt-0 lg:grid lg:grid-cols-3 xl:grid-cols-4`}>
        {fields.map((f) => (
          <label key={f.name} className="block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{f.label}</span>
            {f.kind === "select" ? (
              <select value={current(f.name)} onChange={(e) => push({ [f.name]: e.target.value })}
                className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-brand-600">
                {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            ) : f.kind === "toggle" ? (
              <button type="button" onClick={() => push({ [f.name]: current(f.name) ? "" : "true" })}
                className={`w-full rounded-xl border px-3 py-2.5 text-sm font-semibold transition ${current(f.name) ? "border-brand-700 bg-brand-50 text-brand-800" : "border-slate-300 bg-white text-slate-600"}`}>
                {current(f.name) ? "✓ On" : "Off"}
              </button>
            ) : (
              <input
                type={f.kind === "number" ? "number" : "text"}
                defaultValue={current(f.name)}
                placeholder={f.placeholder}
                onBlur={(e) => { if (e.target.value !== current(f.name)) push({ [f.name]: e.target.value }); }}
                onKeyDown={(e) => { if (e.key === "Enter") push({ [f.name]: (e.target as HTMLInputElement).value }); }}
                className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm outline-none focus:border-brand-600"
              />
            )}
          </label>
        ))}

        {sorts && (
          <label className="block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Sort by</span>
            <select value={current("sort")} onChange={(e) => push({ sort: e.target.value })}
              className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-brand-600">
              {sorts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
        )}

        <div className="hidden items-end lg:flex">
          <button type="button" onClick={() => router.push(pathname)}
            className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Reset filters
          </button>
        </div>
      </div>
    </div>
  );
}

/** Server-rendered pagination driven by the same query string. */
export function Pager({ total, page, pageSize, baseQuery }: { total: number; page: number; pageSize: number; baseQuery: string }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  const nums = Array.from({ length: pages }, (_, i) => i + 1)
    .filter((n) => n === 1 || n === pages || Math.abs(n - page) <= 2);
  const sep = baseQuery ? "&" : "";
  return (
    <nav className="mt-8 flex flex-wrap items-center justify-center gap-1.5" aria-label="Pagination">
      {page > 1 && <a href={`?${baseQuery}${sep}page=${page - 1}`} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700">Previous</a>}
      {nums.map((n, i) => (
        <span key={n} className="flex items-center gap-1.5">
          {i > 0 && n - nums[i - 1] > 1 && <span className="px-1 text-slate-400">…</span>}
          <a href={`?${baseQuery}${sep}page=${n}`}
            className={`rounded-lg px-3.5 py-2 text-sm font-semibold ${n === page ? "bg-brand-800 text-white" : "border border-slate-300 bg-white text-slate-700"}`}>
            {n}
          </a>
        </span>
      ))}
      {page < pages && <a href={`?${baseQuery}${sep}page=${page + 1}`} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700">Next</a>}
    </nav>
  );
}
