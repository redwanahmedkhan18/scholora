"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { adminFetch } from "@/lib/admin";
import { RESOURCE_META } from "@/lib/admin-meta";

type Dict = Record<string, any>;

const READONLY = new Set(["id", "created_at", "updated_at", "slug", "impressions", "clicks", "reference"]);

/**
 * Resources whose rows are decisions rather than content (F-2, F-4).
 *
 * The verification register is the evidence an entity's trust is derived from, so its rows cannot
 * be edited or deleted from a generic table: the API answers 405 for both, and this list keeps the
 * screen from offering an action that cannot succeed. Withdrawal is a transition with a reason and
 * an actor, and it lives in the review queue where the decision is made.
 */
const DECISION_ONLY = new Set(["verifications"]);

function cellText(v: any): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.length ? `${v.length} item(s)` : "—";
  if (typeof v === "object") return "{…}";
  const s = String(v);
  return s.length > 48 ? `${s.slice(0, 48)}…` : s;
}

export default function ResourceManager({ resource }: { resource: string }) {
  const meta = RESOURCE_META[resource];
  const [rows, setRows] = useState<Dict[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Dict | null>(null);
  const [creating, setCreating] = useState(false);
  const pageSize = 25;

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const sp = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      if (q.trim()) sp.set("q", q.trim());
      const data = await adminFetch<Dict>(`/${resource}?${sp.toString()}`);
      setRows(data.items ?? []);
      setTotal(data.total ?? 0);
    } catch (e: any) {
      setError(e?.message ?? "Could not load this resource.");
    } finally {
      setLoading(false);
    }
  }, [resource, page, q]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [resource]);

  const columns = useMemo(() => {
    if (meta?.columns?.length) return meta.columns.filter((c) => rows.length === 0 || c in rows[0]);
    return Object.keys(rows[0] ?? {}).slice(0, 7);
  }, [meta, rows]);

  const template = useMemo(() => {
    const base = rows[0] ?? {};
    const blank: Dict = {};
    Object.entries(base).forEach(([k, v]) => {
      if (READONLY.has(k)) return;
      blank[k] = Array.isArray(v) ? [] : typeof v === "boolean" ? false : typeof v === "number" ? null
        : v !== null && typeof v === "object" ? {} : "";
    });
    return blank;
  }, [rows]);

  const remove = async (row: Dict) => {
    if (!window.confirm(`Delete ${resource} #${row.id}? This cannot be undone.`)) return;
    try {
      await adminFetch(`/${resource}/${row.id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      window.alert(e?.message ?? "Delete failed. Deleting requires senior counselor rights or higher.");
    }
  };

  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">{meta?.label ?? resource}</h1>
          <p className="text-sm text-slate-500">{total} record{total === 1 ? "" : "s"}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} placeholder="Search…"
            className="rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-brand-600" />
          <button type="button" onClick={() => { setCreating(true); setEditing({ ...template }); }}
            className="rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">+ New record</button>
          <button type="button" onClick={load} className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-600">Refresh</button>
        </div>
      </div>

      {meta?.hint && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">{meta.hint}</p>
      )}

      {error && <p className="rounded-xl bg-rose-50 p-3 text-sm text-rose-800 ring-1 ring-rose-200">{error}</p>}

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              {columns.map((c) => <th key={c} className="whitespace-nowrap px-3 py-2.5 font-semibold">{c.replace(/_/g, " ")}</th>)}
              <th className="px-3 py-2.5 font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && <tr><td colSpan={columns.length + 1} className="px-3 py-8 text-center text-slate-500">Loading…</td></tr>}
            {!loading && rows.length === 0 && (
              <tr><td colSpan={columns.length + 1} className="px-3 py-8 text-center text-slate-500">No records yet. Use “New record” to add the first one.</td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id} className="hover:bg-slate-50">
                {columns.map((c) => (
                  <td key={c} className="whitespace-nowrap px-3 py-2.5 text-slate-700">
                    {c === "confidence" ? <ConfidenceCell value={r[c]} /> : cellText(r[c])}
                  </td>
                ))}
                <td className="whitespace-nowrap px-3 py-2.5">
                  {DECISION_ONLY.has(resource) ? (
                    <span className="text-xs text-slate-500" title="Registered claims are decided, withdrawn or appended — never edited or deleted.">
                      Review workflow
                    </span>
                  ) : (
                    <>
                      <button type="button" onClick={() => { setCreating(false); setEditing(r); }}
                        className="rounded-lg bg-brand-50 px-2.5 py-1 text-xs font-bold text-brand-800">Edit</button>
                      <button type="button" onClick={() => remove(r)}
                        className="ml-1.5 rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-600">Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-semibold disabled:opacity-40">Previous</button>
          <span className="text-sm text-slate-600">Page {page} of {pages}</span>
          <button type="button" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-semibold disabled:opacity-40">Next</button>
        </div>
      )}

      {editing && (
        <RecordEditor resource={resource} record={editing} creating={creating}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />
      )}
    </div>
  );
}

function ConfidenceCell({ value }: { value?: string }) {
  const map: Dict = {
    VERIFIED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    NEEDS_REVIEW: "bg-amber-50 text-amber-700 ring-amber-200",
    OUTDATED: "bg-orange-50 text-orange-700 ring-orange-200",
    CONFLICTING: "bg-rose-50 text-rose-700 ring-rose-200",
    UNVERIFIED: "bg-slate-100 text-slate-600 ring-slate-200",
  };
  if (!value) return <span className="text-slate-400">—</span>;
  return <span className={`chip ring-1 ${map[value] ?? map.UNVERIFIED}`}>{value.replace(/_/g, " ").toLowerCase()}</span>;
}

/* ------------------------------------------------------------- editor */
function RecordEditor({ resource, record, creating, onClose, onSaved }: {
  resource: string; record: Dict; creating: boolean; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<Dict>(() => {
    const copy: Dict = {};
    Object.entries(record).forEach(([k, v]) => { if (!READONLY.has(k) || creating === false) copy[k] = v; });
    return copy;
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [raw, setRaw] = useState(false);
  const [rawText, setRawText] = useState("");

  const set = (k: string, v: any) => setForm((f) => ({ ...f, [k]: v }));

  const save = async () => {
    setBusy(true); setErr("");
    try {
      let payload: Dict;
      if (raw) {
        payload = JSON.parse(rawText);
      } else {
        payload = {};
        Object.entries(form).forEach(([k, v]) => { if (!READONLY.has(k)) payload[k] = v; });
      }
      if (creating) await adminFetch(`/${resource}`, { method: "POST", body: JSON.stringify(payload) });
      else await adminFetch(`/${resource}/${record.id}`, { method: "PUT", body: JSON.stringify(payload) });
      onSaved();
    } catch (e: any) {
      setErr(e?.message ?? "Save failed.");
    } finally {
      setBusy(false);
    }
  };

  const entries = Object.entries(form).filter(([k]) => !READONLY.has(k) || k === "id");

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/40" onClick={onClose}>
      <div className="h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-slate-900">
              {creating ? "New" : "Edit"} {RESOURCE_META[resource]?.label ?? resource}
              {!creating && <span className="ml-2 text-sm font-normal text-slate-500">#{record.id}</span>}
            </h2>
            <p className="text-xs text-slate-500">Changes take effect on the public site at the next revalidation.</p>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold">Close</button>
        </div>

        <div className="mt-3 flex gap-2">
          <button type="button" onClick={() => setRaw(false)}
            className={`rounded-lg px-3 py-1.5 text-xs font-bold ${raw ? "bg-slate-100 text-slate-600" : "bg-brand-800 text-white"}`}>Form</button>
          <button type="button"
            onClick={() => { setRawText(JSON.stringify(Object.fromEntries(Object.entries(form).filter(([k]) => !READONLY.has(k))), null, 2)); setRaw(true); }}
            className={`rounded-lg px-3 py-1.5 text-xs font-bold ${raw ? "bg-brand-800 text-white" : "bg-slate-100 text-slate-600"}`}>Raw JSON</button>
        </div>

        {err && <p className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 ring-1 ring-rose-200">{err}</p>}

        {raw ? (
          <textarea value={rawText} onChange={(e) => setRawText(e.target.value)} spellCheck={false}
            className="mt-4 h-[60vh] w-full rounded-xl border border-slate-300 p-3 font-mono text-xs outline-none focus:border-brand-600" />
        ) : (
          <div className="mt-4 space-y-3">
            {entries.map(([key, value]) => (
              <Field key={key} name={key} value={value} disabled={READONLY.has(key)} onChange={(v) => set(key, v)} />
            ))}
          </div>
        )}

        <div className="sticky bottom-0 mt-6 flex gap-2 border-t border-slate-200 bg-white pt-4">
          <button type="button" onClick={save} disabled={busy}
            className="rounded-xl bg-brand-800 px-6 py-2.5 text-sm font-bold text-white disabled:opacity-60">
            {busy ? "Saving…" : creating ? "Create record" : "Save changes"}
          </button>
          <button type="button" onClick={onClose} className="rounded-xl border border-slate-300 px-5 py-2.5 text-sm font-semibold text-slate-600">Cancel</button>
        </div>
      </div>
    </div>
  );
}

function Field({ name, value, onChange, disabled }: { name: string; value: any; onChange: (v: any) => void; disabled?: boolean }) {
  const label = name.replace(/_/g, " ");
  const base = "w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-600 disabled:bg-slate-50 disabled:text-slate-400";

  if (typeof value === "boolean") {
    return (
      <label className="flex items-center gap-2.5 text-sm text-slate-700">
        <input type="checkbox" checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 rounded border-slate-300" />
        <span className="font-medium capitalize">{label}</span>
      </label>
    );
  }

  if (Array.isArray(value) || (value !== null && typeof value === "object")) {
    return (
      <label className="block">
        <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label} (JSON)</span>
        <textarea defaultValue={JSON.stringify(value, null, 2)} spellCheck={false} disabled={disabled}
          onBlur={(e) => { try { onChange(JSON.parse(e.target.value || "null")); } catch { /* keep previous */ } }}
          className={`${base} min-h-24 font-mono text-xs`} />
      </label>
    );
  }

  const isLong = typeof value === "string" && (value.length > 110 || /description|overview|body|summary|bio|notes|answer|testimonial|address|text/.test(name));
  const isNumber = typeof value === "number";

  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {isLong ? (
        <textarea value={value ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={`${base} min-h-24`} />
      ) : (
        <input type={isNumber ? "number" : "text"} step="any" value={value ?? ""} disabled={disabled}
          onChange={(e) => onChange(isNumber ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
          className={base} />
      )}
    </label>
  );
}
