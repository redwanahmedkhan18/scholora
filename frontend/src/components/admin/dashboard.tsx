"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { adminFetch } from "@/lib/admin";
import { dateLabel } from "@/lib/format";

type Dict = Record<string, any>;

export default function AdminDashboard() {
  const [data, setData] = useState<Dict | null>(null);
  const [analytics, setAnalytics] = useState<Dict | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    Promise.all([adminFetch<Dict>("/dashboard"), adminFetch<Dict>("/analytics")])
      .then(([d, a]) => { setData(d); setAnalytics(a); })
      .catch((e) => setErr(e?.message ?? "Could not load the dashboard."));
  }, []);

  if (err) return <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">{err}</p>;
  if (!data) return <p className="text-sm text-slate-500">Loading dashboard…</p>;

  const c = data.counters ?? {};
  const tiles: [string, string, string | undefined][] = [
    ["New leads", c.new_leads, "/admin/leads"],
    ["Leads (7 days)", c.leads_7d, "/admin/leads"],
    ["Active students", c.active_students, undefined],
    ["Appointments", c.appointments, "/admin/appointments"],
    ["Applications", c.applications, undefined],
    ["Offers", c.offers, undefined],
    ["Visa cases", c.visa_cases, undefined],
    ["Brochures generated", c.brochures, undefined],
    ["Universities", c.universities, "/admin/universities"],
    ["Programmes", c.programs, "/admin/programs"],
    ["Scholarships", c.scholarships, "/admin/scholarships"],
    ["Faculty", c.professors, "/admin/professors"],
    ["WhatsApp open", c.whatsapp_open, undefined],
    ["Needs verification", c.needs_verification, "/admin/verifications"],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Dashboard</h1>
        <p className="text-sm text-slate-500">Operational snapshot across content, data quality, CRM and marketing.</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
        {tiles.map(([label, value, href]) => {
          const body = (
            <>
              <p className="text-2xl font-black text-slate-900">{value ?? 0}</p>
              <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
            </>
          );
          return href
            ? <Link key={label} href={href} className="rounded-2xl border border-slate-200 bg-white p-4 transition hover:border-brand-300">{body}</Link>
            : <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4">{body}</div>;
        })}
      </div>

      {Number(c.needs_verification) > 0 && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
          <p className="text-sm font-bold text-amber-900">{c.needs_verification} record(s) need verification attention</p>
          <p className="mt-1 text-xs leading-relaxed text-amber-900">
            Conflicting or outdated sources must be resolved by re-checking the official page — never by silently
            overwriting one value with another.
          </p>
          <Link href="/admin/verifications" className="mt-3 inline-block rounded-xl bg-amber-500 px-4 py-2 text-xs font-bold text-amber-950">
            Open the verification queue
          </Link>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Lead pipeline" href="/admin/leads">
          <Bars rows={(data.lead_pipeline ?? []).map((x: Dict) => ({ label: x.stage, value: x.count }))} />
        </Panel>
        <Panel title="Lead sources" href="/admin/leads">
          <Bars rows={(data.lead_sources ?? []).map((x: Dict) => ({ label: x.source, value: x.count }))} />
        </Panel>
        <Panel title="What students search for">
          <Bars rows={(data.top_searches ?? []).slice(0, 6).map((x: Dict) => ({
            label: [x.subject, x.country, x.degree_level].filter(Boolean).join(" · ") || "(no filters)",
            value: x.count,
          }))} />
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Upcoming application deadlines" href="/admin/program-intakes">
          <ul className="divide-y divide-slate-100">
            {(data.upcoming_deadlines ?? []).slice(0, 8).map((d: Dict, i: number) => (
              <li key={i} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="truncate text-slate-700">{d.program ?? d.label}</span>
                <span className="shrink-0 font-semibold text-slate-900">{dateLabel(d.deadline)}</span>
              </li>
            ))}
            {(data.upcoming_deadlines ?? []).length === 0 && <li className="py-3 text-sm text-slate-500">No deadlines recorded.</li>}
          </ul>
        </Panel>

        <Panel title="Recent activity">
          <ul className="divide-y divide-slate-100">
            {(analytics?.events ?? []).map((e: Dict) => (
              <li key={e.name} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="text-slate-700">{e.name.replace(/_/g, " ")}</span>
                <span className="font-semibold text-slate-900">{e.count}</span>
              </li>
            ))}
            {(analytics?.events ?? []).length === 0 && <li className="py-3 text-sm text-slate-500">No tracked events yet.</li>}
          </ul>
          <p className="mt-3 text-xs text-slate-500">{analytics?.search_total ?? 0} searches logged in total.</p>
        </Panel>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Quick actions</h2>
        <div className="mt-3 flex flex-wrap gap-2">
          {[["/admin/homepage-slides", "Edit rotating hero"], ["/admin/programs", "Add a programme"],
            ["/admin/scholarships", "Add a scholarship"], ["/admin/events", "Publish an event"],
            ["/admin/campaigns", "Launch a campaign"], ["/admin/marketing", "Marketing & pixels"],
            ["/admin/settings", "Site settings"]].map(([href, label]) => (
            <Link key={href} href={href} className="rounded-xl bg-slate-100 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-200">{label}</Link>
          ))}
        </div>
      </div>
    </div>
  );
}

function Panel({ title, href, children }: { title: string; href?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">{title}</h2>
        {href && <Link href={href} className="text-[11px] font-semibold text-brand-800">Open →</Link>}
      </div>
      {children}
    </section>
  );
}

function Bars({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (rows.length === 0) return <p className="text-sm text-slate-500">No data yet.</p>;
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.label}>
          <div className="flex items-center justify-between text-xs">
            <span className="truncate text-slate-600">{r.label}</span>
            <span className="font-semibold text-slate-900">{r.value}</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <div className="h-full rounded-full bg-brand-700" style={{ width: `${(r.value / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}
