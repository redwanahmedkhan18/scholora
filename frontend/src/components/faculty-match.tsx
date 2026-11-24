"use client";

import Link from "next/link";
import { useState } from "react";
import { EMAIL_STATUS_LABEL } from "@/lib/format";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

/** PhD supervisor matching (§26). Only shows overlap that the stored record actually evidences. */
export default function FacultyMatch({ countries, areas }: { countries: Dict[]; areas: Dict[] }) {
  const [interests, setInterests] = useState("");
  const [country, setCountry] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Dict[] | null>(null);
  const [error, setError] = useState("");

  const run = async (raw?: string) => {
    const text = (raw ?? interests).trim();
    if (!text) { setError("Type at least one research interest, for example: clinical nlp, federated learning."); return; }
    setLoading(true); setError(""); setResults(null);
    track("faculty_match_run", { interests: text, country });
    try {
      const res = await fetch("/api/v1/match/faculty", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          interests: text.split(",").map((s) => s.trim()).filter(Boolean),
          country_slug: country || null,
          limit: 12,
        }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setResults(await res.json());
    } catch {
      setError("The matching service did not respond. Please try again, or message us on WhatsApp.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card p-6">
      <h2 className="text-lg font-bold text-slate-900">Find a supervisor by research overlap</h2>
      <p className="mt-1 text-sm text-slate-600">
        Type your research interests. We compare them against each professor&apos;s recorded interests, keywords and
        publications, and show you the evidence for every match. No overlap, no match — we will not pad the list.
      </p>

      <div className="mt-4 grid gap-3 sm:grid-cols-[2fr_1fr_auto]">
        <input value={interests} onChange={(e) => setInterests(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") run(); }}
          placeholder="clinical nlp, federated learning, medical imaging"
          className="rounded-xl border border-slate-300 px-3.5 py-3 text-sm outline-none focus:border-brand-600" />
        <select value={country} onChange={(e) => setCountry(e.target.value)}
          className="rounded-xl border border-slate-300 bg-white px-3.5 py-3 text-sm outline-none focus:border-brand-600">
          <option value="">Any destination</option>
          {countries.filter((c) => c.status !== "COMING_SOON").map((c) => (
            <option key={c.id} value={c.slug}>{c.flag_emoji} {c.name}</option>
          ))}
        </select>
        <button type="button" onClick={() => run()} disabled={loading}
          className="rounded-xl bg-brand-800 px-6 py-3 text-sm font-bold text-white disabled:opacity-60">
          {loading ? "Matching…" : "Match supervisors"}
        </button>
      </div>

      {areas.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Try:</span>
          {areas.slice(0, 8).map((a) => (
            <button key={a.id} type="button"
              onClick={() => { const v = (a.keywords ?? [a.name]).slice(0, 2).join(", "); setInterests(v); run(v); }}
              className="chip bg-slate-100 text-slate-700 hover:bg-slate-200">
              {a.name}
            </button>
          ))}
        </div>
      )}

      {error && <p className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 ring-1 ring-rose-200">{error}</p>}

      {results && (
        <div className="mt-6">
          <p className="text-sm font-semibold text-slate-900">
            {results.length} supervisor{results.length === 1 ? "" : "s"} with documented overlap
          </p>
          {results.length === 0 && (
            <p className="mt-2 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
              No professor in our database lists those interests. That means we have no evidence — not that nobody
              works on it. Ask a counselor and we will research the departments for you.
            </p>
          )}
          <ul className="mt-3 space-y-3">
            {results.map((f) => (
              <li key={f.id} className="rounded-xl border border-slate-200 p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <Link href={`/faculty/${f.slug}`} className="text-base font-bold text-brand-800">{f.full_name}</Link>
                    <p className="text-xs text-slate-600">
                      {f.title} · {f.university?.country?.flag_emoji} {f.university?.name}
                      {f.department?.name ? ` · ${f.department.name}` : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {typeof f.match_score === "number" && <span className="chip bg-brand-50 text-brand-800">Overlap score {Math.round(f.match_score)}</span>}
                    {f.accepting_phd === "YES" && <span className="chip bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">Accepting PhD students</span>}
                  </div>
                </div>

                {(f.matched_interests ?? []).length > 0 && (
                  <p className="mt-2 text-xs text-slate-700">
                    <strong className="text-slate-900">Matched on:</strong> {f.matched_interests.join(", ")}
                  </p>
                )}
                {(f.evidence ?? []).length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {f.evidence.slice(0, 3).map((e: any, i: number) => (
                      <li key={i} className="text-[11px] leading-relaxed text-slate-600">
                        — {typeof e === "string" ? e : e.detail ?? e.title ?? JSON.stringify(e)}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-2 text-[11px] text-slate-500">
                  Contact: {f.email ? <a href={`mailto:${f.email}`} className="font-semibold text-brand-700">{f.email}</a> : "not publicly listed"}
                  {" · "}{EMAIL_STATUS_LABEL[f.email_status] ?? "Source not recorded"}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
