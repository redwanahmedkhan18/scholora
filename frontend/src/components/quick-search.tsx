"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

/** Homepage search bar - posts straight into the /programs search matrix. */
export default function QuickSearch({ countries, degreeLevels = [], title, subtitle }:
  { countries: Dict[]; degreeLevels?: Array<{ value: string; label: string }>; title?: string; subtitle?: string | null }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [country, setCountry] = useState("");
  const [level, setLevel] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const sp = new URLSearchParams();
    if (q.trim()) sp.set("q", q.trim());
    if (country) sp.set("country", country);
    if (level) sp.set("degree_level", level);
    track("home_search", { q, country, level }, "Search");
    router.push(`/programs?${sp.toString()}`);
  };

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6">
      <form onSubmit={submit} className="card -mt-8 grid gap-3 p-4 sm:p-5 lg:grid-cols-[1.6fr_1fr_1fr_auto]">
        <div className="lg:col-span-4">
          <h2 className="text-lg font-bold text-slate-900">{title ?? "Find your university"}</h2>
          {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        </div>
        <label className="block">
          <span className="sr-only">Subject, programme or university</span>
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Computer science, MBA, University of Windsor…"
            className="w-full rounded-xl border border-slate-300 px-3.5 py-3 text-sm outline-none focus:border-brand-600" />
        </label>
        <label className="block">
          <span className="sr-only">Destination</span>
          <select value={country} onChange={(e) => setCountry(e.target.value)}
            className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-3 text-sm outline-none focus:border-brand-600">
            <option value="">Any destination</option>
            {countries.filter((c) => c.status !== "COMING_SOON").map((c) => (
              <option key={c.id} value={c.slug}>{c.flag_emoji} {c.name}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="sr-only">Degree level</span>
          <select value={level} onChange={(e) => setLevel(e.target.value)}
            className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-3 text-sm outline-none focus:border-brand-600">
            <option value="">Any level</option>
            {degreeLevels.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
        </label>
        <button type="submit" className="rounded-xl bg-brand-800 px-6 py-3 text-sm font-bold text-white transition hover:bg-brand-700">
          Search
        </button>
      </form>
    </div>
  );
}
