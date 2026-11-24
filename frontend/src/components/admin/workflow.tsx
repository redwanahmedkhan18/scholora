"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AuthError, adminFetch } from "@/lib/admin";
import { DOC_LABELS, STATUS_LABELS, statusTone } from "@/lib/portal";

type Dict = Record<string, any>;

/* ------------------------------------------------------------- primitives */
/**
 * Admin data access, backed by TanStack Query.
 *
 * Deliberately keeps the `{ data, error, loading, reload }` shape the panels already use, so
 * adopting Query was a one-function change rather than a rewrite of ten call sites. What the
 * panels gain for free: requests for the same path are deduped across panels, results stay warm
 * for 30s when switching tabs, and a stale view refreshes when the operator returns to the
 * window - which matters for a lead board two people work at once.
 */
function useResource<T = Dict>(path: string) {
  const queryClient = useQueryClient();

  const query = useQuery<T, Error>({
    queryKey: ["admin", path],
    queryFn: () => adminFetch<T>(path),
  });

  const reload = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["admin", path] }),
    [queryClient, path],
  );

  const error = query.error
    ? query.error instanceof AuthError
      ? query.error.message
      : (query.error.message ?? "Could not load this view.")
    : "";

  return { data: query.data ?? null, error, loading: query.isPending, reload };
}

function Panel({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-bold text-slate-900">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Kpi({ value, label, tone = "brand" }: { value: any; label: string; tone?: string }) {
  const tones: Dict = {
    brand: "bg-brand-50 text-brand-900 ring-brand-100",
    amber: "bg-amber-50 text-amber-900 ring-amber-200",
    rose: "bg-rose-50 text-rose-900 ring-rose-200",
    emerald: "bg-emerald-50 text-emerald-900 ring-emerald-200",
  };
  return (
    <div className={`rounded-2xl p-4 ring-1 ${tones[tone] ?? tones.brand}`}>
      <p className="text-2xl font-black">{value ?? 0}</p>
      <p className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide opacity-70">{label}</p>
    </div>
  );
}

function Loading({ error, loading }: { error: string; loading: boolean }) {
  if (error) return <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">{error}</p>;
  if (loading) return <p className="py-10 text-center text-sm text-slate-500">Loading…</p>;
  return null;
}

/* ================================================== counselor dashboard */
export function CounselorDashboard() {
  const { data, error, loading, reload } = useResource("/counselor/dashboard");
  if (!data) return <Loading error={error} loading={loading} />;
  const k = data.kpis ?? {};

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-black text-slate-900">My desk</h1>
          <p className="text-sm text-slate-600">{data.counselor?.name} · {data.counselor?.role?.replace(/_/g, " ")}</p>
        </div>
        <button type="button" onClick={reload} className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300">
          Refresh
        </button>
      </header>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi value={k.my_leads} label="My leads" />
        <Kpi value={k.my_applications} label="Applications" />
        <Kpi value={k.appointments_upcoming} label="Appointments" />
        <Kpi value={k.documents_awaiting_review} label="Docs to review" tone="amber" />
        <Kpi value={k.needs_follow_up} label="Idle 7+ days" tone="rose" />
        <Kpi value={k.unread_notifications} label="Unread" tone="emerald" />
      </div>

      {!!data.follow_ups?.length && (
        <Panel title="Chase these first">
          <ul className="divide-y divide-slate-100">
            {data.follow_ups.map((l: Dict) => (
              <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                <span className="font-semibold text-slate-800">{l.full_name}</span>
                <span className="text-slate-500">{l.phone}</span>
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-600">{l.stage}</span>
                <span className="text-xs font-bold text-rose-700">{l.days_idle} days idle</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="My leads" action={<Link href="/admin/leads" className="text-xs font-bold text-brand-800 underline">All leads</Link>}>
          {!data.leads?.length ? <p className="text-sm text-slate-500">Nothing assigned yet.</p> : (
            <ul className="divide-y divide-slate-100">
              {data.leads.slice(0, 10).map((l: Dict) => (
                <li key={l.id} className="py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-slate-800">{l.full_name}</span>
                    <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[10px] font-bold text-brand-800">{l.stage}</span>
                  </div>
                  <p className="text-[11px] text-slate-500">{l.phone} · {l.source}{l.interest ? ` · ${l.interest}` : ""}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Applications in flight" action={<Link href="/admin/applications" className="text-xs font-bold text-brand-800 underline">Pipeline board</Link>}>
          {!data.applications?.length ? <p className="text-sm text-slate-500">No applications yet.</p> : (
            <ul className="divide-y divide-slate-100">
              {data.applications.slice(0, 10).map((a: Dict) => (
                <li key={a.id} className="py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-slate-800">{a.student ?? a.reference}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ring-1 ${statusTone(a.status)}`}>
                      {STATUS_LABELS[a.status] ?? a.status}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-500">{a.program} — {a.university}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Upcoming appointments" action={<Link href="/admin/appointments" className="text-xs font-bold text-brand-800 underline">All</Link>}>
          {!data.appointments?.length ? <p className="text-sm text-slate-500">Nothing booked.</p> : (
            <ul className="divide-y divide-slate-100">
              {data.appointments.map((a: Dict) => (
                <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                  <span className="font-semibold text-slate-800">{a.full_name}</span>
                  <span className="text-slate-500">{String(a.date).slice(0, 10)} {a.time}</span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600">{a.mode}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Documents awaiting review" action={<Link href="/admin/documents" className="text-xs font-bold text-brand-800 underline">Review queue</Link>}>
          {!data.documents_pending?.length ? <p className="text-sm text-slate-500">Queue is clear.</p> : (
            <ul className="divide-y divide-slate-100">
              {data.documents_pending.slice(0, 8).map((d: Dict) => (
                <li key={d.id} className="flex items-center justify-between gap-2 py-2.5 text-sm">
                  <span className="font-semibold text-slate-800">{DOC_LABELS[d.doc_type] ?? d.doc_type}</span>
                  <span className="truncate text-[11px] text-slate-500">{d.filename}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

/* ================================================== applications board */
export function ApplicationsBoard() {
  const { data, error, loading, reload } = useResource("/applications/board");
  const [busy, setBusy] = useState<number | null>(null);
  const [filter, setFilter] = useState("");

  const move = async (id: number, status: string) => {
    const note = window.prompt(`Note for the "${STATUS_LABELS[status] ?? status}" change (optional):`) ?? "";
    setBusy(id);
    try {
      await adminFetch(`/applications/${id}/status`, {
        method: "POST", body: JSON.stringify({ status, note: note || null }),
      });
      reload();
    } catch (e: any) {
      alert(e?.message ?? "Could not update the status.");
    } finally { setBusy(null); }
  };

  if (!data) return <Loading error={error} loading={loading} />;
  const items = filter ? data.items.filter((i: Dict) => i.status === filter) : data.items;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-black text-slate-900">Application pipeline</h1>
          <p className="text-sm text-slate-600">
            {data.total} application{data.total === 1 ? "" : "s"}. Every status change is written to the
            audit log and notifies the student.
          </p>
        </div>
        <button type="button" onClick={reload} className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300">
          Refresh
        </button>
      </header>

      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => setFilter("")}
          className={`rounded-full px-3 py-1.5 text-xs font-bold ${!filter ? "bg-brand-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-300"}`}>
          All ({data.total})
        </button>
        {data.stages.filter((s: string) => data.counts[s]).map((s: string) => (
          <button key={s} type="button" onClick={() => setFilter(s)}
            className={`rounded-full px-3 py-1.5 text-xs font-bold ${filter === s ? "bg-brand-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-300"}`}>
            {STATUS_LABELS[s] ?? s} ({data.counts[s]})
          </button>
        ))}
      </div>

      {!items.length ? (
        <p className="rounded-2xl bg-white p-10 text-center text-sm text-slate-500 ring-1 ring-slate-200">
          No applications in this stage.
        </p>
      ) : (
        <div className="space-y-3">
          {items.map((a: Dict) => (
            <article key={a.id} className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-bold text-slate-900">
                    {a.student?.name ?? "Unknown student"}
                    <span className="ml-2 font-mono text-[11px] font-normal text-slate-400">{a.reference}</span>
                  </p>
                  <p className="text-sm text-slate-600">{a.program?.name} — {a.university?.name}</p>
                  <p className="text-[11px] text-slate-500">
                    {a.student?.phone}{a.student?.email ? ` · ${a.student.email}` : ""} · {a.intake_label ?? "intake TBC"}
                  </p>
                </div>
                <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ring-1 ${statusTone(a.status)}`}>
                  {STATUS_LABELS[a.status] ?? a.status}
                </span>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <select
                  defaultValue=""
                  disabled={busy === a.id}
                  onChange={(e) => { if (e.target.value) { void move(a.id, e.target.value); e.target.value = ""; } }}
                  className="rounded-xl border border-slate-300 px-3 py-1.5 text-xs font-semibold"
                >
                  <option value="">Move to…</option>
                  {data.stages.map((s: string) => (
                    <option key={s} value={s} disabled={s === a.status}>{STATUS_LABELS[s] ?? s}</option>
                  ))}
                </select>
                {a.student?.token && (
                  <Link href={`/my/${a.student.token}`} target="_blank"
                    className="rounded-xl bg-white px-3 py-1.5 text-xs font-bold text-brand-800 ring-1 ring-slate-300">
                    Student portal ↗
                  </Link>
                )}
                {a.program?.slug && (
                  <Link href={`/programs/${a.program.slug}`} target="_blank"
                    className="rounded-xl bg-white px-3 py-1.5 text-xs font-bold text-slate-600 ring-1 ring-slate-300">
                    Programme ↗
                  </Link>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

/* ==================================================== document review */
export function DocumentQueue() {
  const { data, error, loading, reload } = useResource("/documents/queue");
  const [busy, setBusy] = useState<number | null>(null);
  const [filter, setFilter] = useState("");

  const review = async (id: number, status: string) => {
    const note = status === "REJECTED"
      ? window.prompt("Tell the student what to fix:") ?? ""
      : window.prompt("Review note (optional):") ?? "";
    if (status === "REJECTED" && !note.trim()) { alert("A rejection needs a reason the student can act on."); return; }
    setBusy(id);
    try {
      await adminFetch(`/documents/${id}/review`, {
        method: "POST", body: JSON.stringify({ status, review_note: note || null }),
      });
      reload();
    } catch (e: any) { alert(e?.message ?? "Could not save the review."); }
    finally { setBusy(null); }
  };

  if (!data) return <Loading error={error} loading={loading} />;
  const items = filter ? data.items.filter((i: Dict) => i.status === filter) : data.items;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-black text-slate-900">Document review</h1>
          <p className="text-sm text-slate-600">
            Files are stored privately. Each “Open” below mints a fresh link that expires in minutes and
            is recorded in the audit log.
          </p>
        </div>
        <button type="button" onClick={reload} className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300">
          Refresh
        </button>
      </header>

      <div className="flex flex-wrap gap-1.5">
        <button type="button" onClick={() => setFilter("")}
          className={`rounded-full px-3 py-1.5 text-xs font-bold ${!filter ? "bg-brand-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-300"}`}>
          All ({data.total})
        </button>
        {data.statuses.map((s: string) => (
          <button key={s} type="button" onClick={() => setFilter(s)}
            className={`rounded-full px-3 py-1.5 text-xs font-bold ${filter === s ? "bg-brand-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-300"}`}>
            {s.replace(/_/g, " ")} ({data.counts[s] ?? 0})
          </button>
        ))}
      </div>

      {!items.length ? (
        <p className="rounded-2xl bg-white p-10 text-center text-sm text-slate-500 ring-1 ring-slate-200">Nothing here.</p>
      ) : (
        <div className="overflow-x-auto rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Student</th>
                <th className="px-4 py-3">Document</th>
                <th className="px-4 py-3">Uploaded</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((d: Dict) => (
                <tr key={d.id}>
                  <td className="px-4 py-3">
                    <p className="font-semibold text-slate-800">{d.student?.name ?? "—"}</p>
                    {d.student?.token && (
                      <Link href={`/my/${d.student.token}`} target="_blank" className="text-[11px] text-brand-700 underline">
                        portal ↗
                      </Link>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <p className="font-semibold text-slate-800">{DOC_LABELS[d.doc_type] ?? d.doc_type}</p>
                    <p className="text-[11px] text-slate-500">{d.original_filename} · {Math.round((d.size_bytes ?? 0) / 1024)} KB</p>
                    {d.review_note && <p className="text-[11px] text-slate-600">“{d.review_note}”</p>}
                  </td>
                  <td className="px-4 py-3 text-[11px] text-slate-500">{String(d.uploaded_at).slice(0, 16).replace("T", " ")}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-1 text-[10px] font-bold ring-1 ${
                      d.status === "ACCEPTED" ? "bg-emerald-100 text-emerald-800 ring-emerald-200"
                      : d.status === "REJECTED" ? "bg-rose-100 text-rose-800 ring-rose-200"
                      : "bg-slate-100 text-slate-700 ring-slate-200"}`}>
                      {d.status.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      <a href={d.download_url} target="_blank" rel="noopener noreferrer"
                        className="rounded-lg bg-white px-2.5 py-1.5 text-[11px] font-bold text-brand-800 ring-1 ring-slate-300">Open</a>
                      <button type="button" disabled={busy === d.id} onClick={() => review(d.id, "ACCEPTED")}
                        className="rounded-lg bg-emerald-600 px-2.5 py-1.5 text-[11px] font-bold text-white disabled:opacity-50">Accept</button>
                      <button type="button" disabled={busy === d.id} onClick={() => review(d.id, "REJECTED")}
                        className="rounded-lg bg-rose-600 px-2.5 py-1.5 text-[11px] font-bold text-white disabled:opacity-50">Reject</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ===================================================== notifications */
export function NotificationInbox() {
  const { data, error, loading, reload } = useResource("/notifications/inbox");

  const readOne = async (id: number) => { await adminFetch(`/notifications/${id}/read`, { method: "POST" }); reload(); };
  const readAll = async () => { await adminFetch("/notifications/read-all", { method: "POST" }); reload(); };

  if (!data) return <Loading error={error} loading={loading} />;

  const tone: Dict = {
    CRITICAL: "border-l-rose-500 bg-rose-50/40",
    WARNING: "border-l-amber-400 bg-amber-50/40",
    INFO: "border-l-brand-600",
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-black text-slate-900">Notifications</h1>
          <p className="text-sm text-slate-600">{data.unread} unread</p>
        </div>
        <button type="button" onClick={readAll} className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300">
          Mark all read
        </button>
      </header>

      {!data.items.length ? (
        <p className="rounded-2xl bg-white p-10 text-center text-sm text-slate-500 ring-1 ring-slate-200">Inbox is empty.</p>
      ) : (
        <ul className="space-y-2.5">
          {data.items.map((n: Dict) => (
            <li key={n.id} className={`rounded-2xl border-l-4 bg-white p-4 shadow-sm ring-1 ring-slate-200 ${tone[n.severity] ?? tone.INFO} ${n.read ? "opacity-60" : ""}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-bold text-slate-900">{n.title}</p>
                  <p className="mt-0.5 text-sm text-slate-600">{n.body}</p>
                  <p className="mt-1 text-[11px] text-slate-400">
                    {n.trigger} · {n.channel} · {String(n.at).slice(0, 16).replace("T", " ")}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {n.action_url && (
                    <Link href={n.action_url} className="rounded-lg bg-brand-50 px-3 py-1.5 text-[11px] font-bold text-brand-800">Open</Link>
                  )}
                  {!n.read && (
                    <button type="button" onClick={() => readOne(n.id)}
                      className="rounded-lg bg-slate-100 px-3 py-1.5 text-[11px] font-bold text-slate-700">Mark read</button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ====================================================== audit log viewer */
export function AuditLogViewer() {
  const [filters, setFilters] = useState({ actor: "", action: "", entity_type: "", days: "30" });
  const [page, setPage] = useState(1);
  const query = new URLSearchParams(
    Object.entries({ ...filters, page: String(page) }).filter(([, v]) => v) as [string, string][],
  ).toString();
  const { data, error, loading, reload } = useResource(`/audit/log?${query}`);

  if (!data) return <Loading error={error} loading={loading} />;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-black text-slate-900">Audit log</h1>
        <p className="text-sm text-slate-600">
          {data.total} event{data.total === 1 ? "" : "s"} in the last {filters.days} days. Records are
          append-only — they cannot be edited or deleted from the admin.
        </p>
      </header>

      <div className="grid gap-2 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-200 sm:grid-cols-4">
        <input placeholder="Actor email" value={filters.actor}
          onChange={(e) => { setFilters({ ...filters, actor: e.target.value }); setPage(1); }}
          className="rounded-xl border border-slate-300 px-3 py-2 text-sm" />
        <select value={filters.action} onChange={(e) => { setFilters({ ...filters, action: e.target.value }); setPage(1); }}
          className="rounded-xl border border-slate-300 px-3 py-2 text-sm">
          <option value="">All actions</option>
          {(data.filters?.actions ?? []).map((a: string) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={filters.entity_type} onChange={(e) => { setFilters({ ...filters, entity_type: e.target.value }); setPage(1); }}
          className="rounded-xl border border-slate-300 px-3 py-2 text-sm">
          <option value="">All entities</option>
          {(data.filters?.entity_types ?? []).map((a: string) => <option key={a} value={a}>{a}</option>)}
        </select>
        <select value={filters.days} onChange={(e) => { setFilters({ ...filters, days: e.target.value }); setPage(1); }}
          className="rounded-xl border border-slate-300 px-3 py-2 text-sm">
          {["7", "30", "90", "365"].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
      </div>

      <div className="overflow-x-auto rounded-2xl bg-white shadow-sm ring-1 ring-slate-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">When</th>
              <th className="px-4 py-3">Actor</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Entity</th>
              <th className="px-4 py-3">Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.items.map((r: Dict) => (
              <tr key={r.id}>
                <td className="whitespace-nowrap px-4 py-2.5 text-[11px] text-slate-500">
                  {String(r.at).slice(0, 19).replace("T", " ")}
                </td>
                <td className="px-4 py-2.5 text-[12px] font-semibold text-slate-800">{r.actor_email ?? "system"}</td>
                <td className="px-4 py-2.5">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    r.action.includes("DELETE") || r.action.includes("FAILED") ? "bg-rose-100 text-rose-800"
                    : r.action.includes("LOGIN") || r.action.includes("MFA") ? "bg-brand-50 text-brand-800"
                    : "bg-slate-100 text-slate-700"}`}>
                    {r.action}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-[12px] text-slate-600">
                  {r.entity_type}{r.entity_id ? ` #${r.entity_id}` : ""}
                </td>
                <td className="px-4 py-2.5 font-mono text-[10px] text-slate-500">
                  {r.detail && Object.keys(r.detail).length ? JSON.stringify(r.detail).slice(0, 110) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
          className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300 disabled:opacity-40">
          ← Previous
        </button>
        <span className="text-xs text-slate-500">Page {page} of {pages}</span>
        <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}
          className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300 disabled:opacity-40">
          Next →
        </button>
      </div>
    </div>
  );
}

/* ============================================== jobs + assistant health */
export function OperationsPanel() {
  const jobs = useResource("/jobs");
  const convos = useResource("/assistant/conversations?limit=20");
  const providers = useResource("/assistant/providers");
  const [running, setRunning] = useState("");
  const [result, setResult] = useState("");

  const testProvider = async (name: string) => {
    setRunning(`llm:${name}`); setResult("");
    try {
      const res = await adminFetch<Dict>(`/assistant/providers/${name}/test`, { method: "POST" });
      setResult(res.ok
        ? `${res.label}: reachable in ${res.latency_ms} ms using ${res.model} — "${res.reply}"`
        : `${res.label ?? name}: ${res.error}`);
      providers.reload();
    } catch (e: any) { setResult(`${name} test failed — ${e?.message}`); }
    finally { setRunning(""); }
  };

  const run = async (name: string) => {
    setRunning(name); setResult("");
    try {
      const res = await adminFetch<Dict>(`/jobs/${name}/run`, { method: "POST" });
      setResult(`${name}: ${JSON.stringify(res.result)}`);
    } catch (e: any) { setResult(`${name} failed — ${e?.message}`); }
    finally { setRunning(""); }
  };

  const reindex = async () => {
    setRunning("reindex");
    try {
      const res = await adminFetch<Dict>("/assistant/reindex", { method: "POST" });
      setResult(`Knowledge index rebuilt: ${res.total} chunks.`);
      convos.reload();
    } catch (e: any) { setResult(`Reindex failed — ${e?.message}`); }
    finally { setRunning(""); }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-black text-slate-900">Operations</h1>
        <p className="text-sm text-slate-600">
          Scheduled maintenance and the assistant's grounding health.
        </p>
      </header>

      {result && <p className="rounded-xl bg-slate-900 p-3 font-mono text-[11px] text-slate-100">{result}</p>}

      <Panel title={`Scheduled jobs — runner: ${jobs.data?.scheduler ?? "…"}`}>
        {jobs.error && <p className="text-sm text-rose-700">{jobs.error}</p>}
        <ul className="divide-y divide-slate-100">
          {(jobs.data?.jobs ?? []).map((j: Dict) => (
            <li key={j.name} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="font-mono text-[12px] font-bold text-slate-800">{j.name}</p>
                <p className="text-[11px] text-slate-500">
                  {j.description || "Scheduled maintenance task."} · every {Math.round(j.interval_seconds / 60)} min
                </p>
              </div>
              <button type="button" disabled={!!running} onClick={() => run(j.name)}
                className="rounded-xl bg-brand-800 px-3.5 py-1.5 text-xs font-bold text-white disabled:opacity-50">
                {running === j.name ? "Running…" : "Run now"}
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="AI providers">
        {providers.error && <p className="text-sm text-rose-700">{providers.error}</p>}
        {providers.data && (
          <>
            <p className="mb-3 text-[11px] leading-relaxed text-slate-500">
              The assistant can be driven by <strong>Grok, Mistral or Gemini</strong> — whichever you hold a
              key for. They are tried top to bottom and the first one that answers is used; if it fails the
              next takes over automatically. With none configured the assistant still works, answering from
              templates built on verified records instead of generating prose.
              Keys live only in <code className="rounded bg-slate-100 px-1">backend/.env</code> and are never
              shown here or sent to the browser.
            </p>
            <p className="mb-3 text-[11px] font-semibold text-slate-700">
              Current mode:{" "}
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                providers.data.mode === "llm" ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-700"}`}>
                {providers.data.mode === "llm"
                  ? `generating via ${providers.data.active_provider} (${providers.data.active_model})`
                  : "deterministic templates"}
              </span>
            </p>
            <ul className="divide-y divide-slate-100">
              {(providers.data.providers ?? []).map((p: Dict) => (
                <li key={p.provider} className="flex flex-wrap items-start justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="text-[13px] font-bold text-slate-800">
                      <span className="mr-1.5 text-[11px] font-mono text-slate-400">#{p.priority}</span>
                      {p.label}
                      <span className={`ml-2 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        p.configured ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-600"}`}>
                        {p.configured ? "key set" : "not configured"}
                      </span>
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-slate-500">{p.model}</p>
                    {!p.configured && (
                      <p className="text-[11px] text-slate-500">
                        Set <code className="rounded bg-slate-100 px-1">{p.env_key}</code> in backend/.env ·{" "}
                        <a href={p.console_url} target="_blank" rel="noreferrer noopener"
                           className="font-semibold text-brand-800 underline">get a key</a>
                      </p>
                    )}
                    {p.last_error && (
                      <p className="mt-1 rounded-lg bg-rose-50 px-2 py-1 font-mono text-[10px] text-rose-700">
                        last error: {p.last_error}
                      </p>
                    )}
                  </div>
                  <button type="button" disabled={!!running || !p.configured}
                    onClick={() => testProvider(p.provider)}
                    className="rounded-xl bg-brand-800 px-3.5 py-1.5 text-xs font-bold text-white disabled:opacity-40">
                    {running === `llm:${p.provider}` ? "Testing…" : "Test"}
                  </button>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              Whichever provider answers, the reply passes through the same guardrails before a student sees
              it: any sentence claiming an admission chance, a guarantee or a visa prediction is removed, and
              unsourced email addresses are stripped. A model cannot weaken the data-integrity policy.
            </p>
          </>
        )}
      </Panel>

      <Panel title="Assistant grounding"
        action={
          <button type="button" disabled={!!running} onClick={reindex}
            className="rounded-xl bg-brand-800 px-3.5 py-1.5 text-xs font-bold text-white disabled:opacity-50">
            {running === "reindex" ? "Rebuilding…" : "Rebuild index"}
          </button>
        }>
        {convos.data && (
          <>
            <div className="mb-3 grid gap-3 sm:grid-cols-3">
              <Kpi value={convos.data.total} label="Questions answered" />
              <Kpi value={convos.data.ungrounded} label="No verified match" tone="amber" />
              <Kpi value={`${convos.data.ungrounded_rate}%`} label="Ungrounded rate" tone={convos.data.ungrounded_rate > 25 ? "rose" : "emerald"} />
            </div>
            <p className="mb-3 text-[11px] leading-relaxed text-slate-500">
              An ungrounded answer means the assistant found nothing verified and declined to guess —
              that is correct behaviour, but a rising rate shows where the database has gaps worth filling.
            </p>
            <ul className="divide-y divide-slate-100">
              {convos.data.items.slice(0, 12).map((c: Dict) => (
                <li key={c.id} className="py-2.5">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-[13px] font-semibold text-slate-800">{c.question}</p>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                      c.grounded ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>
                      {c.grounded ? "grounded" : "no match"}
                    </span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-slate-500">{c.answer}</p>
                  <p className="text-[10px] text-slate-400">
                    {c.intent} · {c.latency_ms} ms · {(c.sources ?? []).length} source(s)
                    {c.escalated ? " · escalated to a counselor" : ""}
                  </p>
                </li>
              ))}
            </ul>
          </>
        )}
      </Panel>
    </div>
  );
}

/* ============================================================ staff MFA */
export function SecurityPanel() {
  const { data, error, loading, reload } = useResource("/auth/mfa/status");
  const [enrolment, setEnrolment] = useState<Dict | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true); setMsg("");
    try { setEnrolment(await adminFetch<Dict>("/auth/mfa/enroll", { method: "POST" })); }
    catch (e: any) { setMsg(e?.message ?? "Could not start enrolment."); }
    finally { setBusy(false); }
  };

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setMsg("");
    try {
      const res = await adminFetch<Dict>("/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ code }) });
      setCodes(res.recovery_codes ?? []);
      setEnrolment(null); setCode("");
      reload();
    } catch (e: any) { setMsg(e?.message ?? "That code was not accepted."); }
    finally { setBusy(false); }
  };

  if (!data) return <Loading error={error} loading={loading} />;

  return (
    <div className="max-w-2xl space-y-5">
      <header>
        <h1 className="text-xl font-black text-slate-900">My security</h1>
        <p className="text-sm text-slate-600">
          Two-factor authentication for your staff account. Student records and documents sit behind it.
        </p>
      </header>

      <Panel title="Two-factor authentication">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded-full px-3 py-1 text-xs font-bold ring-1 ${
            data.enrolled ? "bg-emerald-100 text-emerald-800 ring-emerald-200" : "bg-amber-100 text-amber-900 ring-amber-200"}`}>
            {data.enrolled ? "Enabled" : "Not enabled"}
          </span>
          {data.required && (
            <span className="text-xs font-semibold text-slate-600">Required for your role</span>
          )}
          {data.enrolled && (
            <span className="text-xs text-slate-500">{data.recovery_codes_remaining} recovery codes left</span>
          )}
        </div>

        {!data.enrolled && !enrolment && (
          <button type="button" onClick={start} disabled={busy}
            className="mt-4 rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60">
            {busy ? "Preparing…" : "Set up authenticator app"}
          </button>
        )}

        {enrolment && (
          <form onSubmit={confirm} className="mt-4 space-y-3">
            <p className="text-sm text-slate-700">{enrolment.instructions}</p>
            <div className="rounded-xl bg-slate-900 p-4">
              <p className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Setup key</p>
              <p className="mt-1 break-all font-mono text-sm font-bold text-emerald-300">{enrolment.manual_entry}</p>
              <p className="mt-2 break-all font-mono text-[9px] text-slate-500">{enrolment.otpauth_url}</p>
            </div>
            <label className="block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                6-digit code from your app
              </span>
              <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" maxLength={6} required
                className="w-40 rounded-xl border border-slate-300 px-3 py-2.5 text-center font-mono text-lg tracking-[0.3em]" />
            </label>
            <button type="submit" disabled={busy}
              className="rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-60">
              {busy ? "Verifying…" : "Confirm and enable"}
            </button>
          </form>
        )}

        {msg && <p className="mt-3 rounded-lg bg-rose-50 p-3 text-xs text-rose-800 ring-1 ring-rose-200">{msg}</p>}

        {codes && (
          <div className="mt-4 rounded-xl bg-amber-50 p-4 ring-1 ring-amber-200">
            <p className="text-sm font-bold text-amber-900">Save your recovery codes now</p>
            <p className="mt-1 text-[11px] text-amber-900/80">
              Each code works once, and they are never shown again. Store them somewhere safe and offline.
            </p>
            <ul className="mt-3 grid grid-cols-2 gap-1.5 font-mono text-sm font-bold text-amber-950">
              {codes.map((c) => <li key={c} className="rounded bg-white/70 px-2 py-1 text-center">{c}</li>)}
            </ul>
          </div>
        )}
      </Panel>

      <p className="rounded-xl bg-slate-100 p-4 text-[11px] leading-relaxed text-slate-600">
        Codes are time-based (TOTP, 30-second window) and work with Google Authenticator, Authy, 1Password
        or any RFC 6238 app. Five wrong codes lock the account for 15 minutes, and every attempt is audited.
      </p>
    </div>
  );
}
