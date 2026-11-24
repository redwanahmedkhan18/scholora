"use client";

import { useCallback, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AuthError, adminFetch } from "@/lib/admin";

type Dict = Record<string, any>;

function useResource<T = Dict>(path: string) {
  const queryClient = useQueryClient();
  const query = useQuery<T, Error>({ queryKey: ["admin", path], queryFn: () => adminFetch<T>(path) });
  const reload = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ["admin", path] }),
    [queryClient, path],
  );
  const error = query.error
    ? query.error instanceof AuthError ? query.error.message : (query.error.message ?? "Could not load.")
    : "";
  return { data: query.data ?? null, error, loading: query.isPending, reload };
}

function Panel({ title, hint, action, children }: {
  title: string; hint?: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold text-slate-900">{title}</h2>
          {hint && <p className="mt-0.5 max-w-2xl text-xs leading-relaxed text-slate-500">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Btn({ children, onClick, tone = "brand", disabled, small }: {
  children: React.ReactNode; onClick?: () => void; tone?: "brand" | "ghost" | "danger";
  disabled?: boolean; small?: boolean;
}) {
  const tones = {
    brand: "bg-brand-900 text-white hover:bg-brand-800 disabled:bg-slate-300",
    ghost: "bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50 disabled:text-slate-400",
    danger: "bg-rose-600 text-white hover:bg-rose-500 disabled:bg-slate-300",
  };
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className={`rounded-lg font-semibold transition ${small ? "px-2.5 py-1 text-[11px]" : "px-3.5 py-2 text-xs"} ${tones[tone]} disabled:cursor-not-allowed`}>
      {children}
    </button>
  );
}

const ACTION_TONE: Dict = {
  CREATE: "bg-emerald-100 text-emerald-800",
  UPDATE: "bg-sky-100 text-sky-800",
  CONFLICT: "bg-amber-100 text-amber-900",
  UNCHANGED: "bg-slate-100 text-slate-500",
  ERROR: "bg-rose-100 text-rose-800",
};

/* ====================================================== import workbench */
/**
 * Bulk loading that cannot violate the integrity policy.
 *
 * Nothing an operator uploads touches a live table. The file is parsed, matched against
 * existing records and diffed field by field; a value that contradicts an already-VERIFIED
 * record is flagged CONFLICT and left unapproved, because "conflicting sources are surfaced,
 * not silently overwritten" is a product rule, not a preference. Committing writes one
 * provenance row per changed field, so every figure on the public site traces to a URL.
 */
export function DataWorkbench() {
  const schemas = useResource<Dict>("/import/schemas");
  const batches = useResource<Dict>("/import/batches");
  const [entityType, setEntityType] = useState("university");
  const [content, setContent] = useState("");
  const [filename, setFilename] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceType, setSourceType] = useState("TIER_1_OFFICIAL_UNIVERSITY");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [openBatch, setOpenBatch] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const pickFile = (file?: File) => {
    if (!file) return;
    setFilename(file.name);
    const reader = new FileReader();
    reader.onload = () => setContent(String(reader.result ?? ""));
    reader.readAsText(file);
  };

  const loadTemplate = async () => {
    try {
      const res = await adminFetch<Dict>(`/import/template/${entityType}`);
      setContent(res.csv);
      setFilename(`${entityType}-template.csv`);
      setMsg("Template loaded — replace the example row with your data.");
    } catch (e: any) { setMsg(e?.message ?? "Could not load template."); }
  };

  const stage = async () => {
    setBusy(true); setMsg("");
    try {
      const res = await adminFetch<Dict>("/import/stage", {
        method: "POST",
        body: JSON.stringify({
          entity_type: entityType, content, filename,
          source_url: sourceUrl, source_type: sourceType, notes,
        }),
      });
      setMsg(`Staged ${res.reference}: ${res.created_rows} new, ${res.updated_rows} updates, ${res.conflict_rows} conflicts, ${res.skipped_rows} skipped. Nothing committed yet.`);
      setContent(""); setFilename("");
      setOpenBatch(res.id);
      await batches.reload();
    } catch (e: any) { setMsg(e?.message ?? "Staging failed."); }
    finally { setBusy(false); }
  };

  const types = schemas.data?.entity_types ?? [];
  const spec = types.find((t: Dict) => t.key === entityType);

  return (
    <div className="space-y-5">
      <Panel
        title="Stage an import"
        hint="CSV or JSON. Nothing is written to live tables until you review the diff and commit."
        action={<Btn tone="ghost" small onClick={loadTemplate}>Download template</Btn>}
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block">
            <span className="text-[11px] font-semibold text-slate-700">Record type</span>
            <select className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
              value={entityType} onChange={(e) => setEntityType(e.target.value)}>
              {types.map((t: Dict) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-[11px] font-semibold text-slate-700">Source URL (batch default)</span>
            <input className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
              placeholder="https://university.edu/admissions"
              value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} />
          </label>
          <label className="block">
            <span className="text-[11px] font-semibold text-slate-700">Source tier</span>
            <select className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
              value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
              {(schemas.data?.source_types ?? []).map((s: string) =>
                <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="text-[11px] font-semibold text-slate-700">Note</span>
            <input className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
              placeholder="e.g. 2026 intake refresh" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
        </div>

        {spec && (
          <p className="mt-2 text-[10px] leading-snug text-slate-500">
            <span className="font-semibold">Required:</span> {spec.required.join(", ")} ·{" "}
            <span className="font-semibold">matched on:</span> {spec.natural_key}
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input ref={fileRef} type="file" accept=".csv,.json,text/csv,application/json" className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0])} />
          <Btn tone="ghost" small onClick={() => fileRef.current?.click()}>Choose file…</Btn>
          {filename && <span className="text-[11px] text-slate-600">{filename}</span>}
        </div>

        <textarea
          className="mt-2 h-40 w-full rounded-lg border border-slate-300 p-2.5 font-mono text-[11px]"
          placeholder="Paste CSV or JSON here, or choose a file above."
          value={content} onChange={(e) => setContent(e.target.value)} />

        <div className="mt-2 flex items-center gap-3">
          <Btn onClick={stage} disabled={busy || !content.trim()}>
            {busy ? "Staging…" : "Stage for review"}
          </Btn>
          {msg && <span className="text-[11px] text-slate-600">{msg}</span>}
        </div>
      </Panel>

      <Panel title="Batches" hint="Review the field-level diff, resolve conflicts, then commit.">
        {batches.error && <p className="text-xs text-rose-700">{batches.error}</p>}
        {(batches.data?.items ?? []).length === 0 && (
          <p className="py-6 text-center text-xs text-slate-500">No imports yet.</p>
        )}
        <div className="space-y-2">
          {(batches.data?.items ?? []).map((b: Dict) => (
            <div key={b.id} className="rounded-xl border border-slate-200">
              <button type="button" onClick={() => setOpenBatch(openBatch === b.id ? null : b.id)}
                className="flex w-full flex-wrap items-center justify-between gap-2 p-3 text-left">
                <span className="flex items-center gap-2">
                  <span className="font-mono text-xs font-bold text-slate-900">{b.reference}</span>
                  <span className="text-[11px] text-slate-500">{b.entity_type} · {b.row_count} rows</span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                    b.status === "COMMITTED" ? "bg-emerald-100 text-emerald-800"
                      : b.status === "DISCARDED" ? "bg-slate-100 text-slate-500"
                        : "bg-amber-100 text-amber-900"}`}>{b.status}</span>
                </span>
                <span className="flex items-center gap-1.5 text-[10px]">
                  {b.created_rows > 0 && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-800">{b.created_rows} new</span>}
                  {b.updated_rows > 0 && <span className="rounded bg-sky-100 px-1.5 py-0.5 text-sky-800">{b.updated_rows} upd</span>}
                  {b.conflict_rows > 0 && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-900">{b.conflict_rows} conflict</span>}
                  {b.skipped_rows > 0 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">{b.skipped_rows} skip</span>}
                </span>
              </button>
              {openBatch === b.id && <BatchDetail id={b.id} onChanged={batches.reload} />}
            </div>
          ))}
        </div>
      </Panel>

      <VerificationQueue />
    </div>
  );
}

function BatchDetail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const { data, error, loading, reload } = useResource<Dict>(`/import/batches/${id}`);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const decide = async (rowId: number, decision: string) => {
    setBusy(true);
    try {
      await adminFetch(`/import/batches/${id}/decide`, {
        method: "POST", body: JSON.stringify({ decisions: { [rowId]: decision } }),
      });
      await reload();
    } catch (e: any) { setMsg(e?.message ?? "Failed."); }
    finally { setBusy(false); }
  };

  const act = async (what: "commit" | "discard") => {
    setBusy(true); setMsg("");
    try {
      const res = await adminFetch<Dict>(`/import/batches/${id}/${what}`, { method: "POST" });
      setMsg(what === "commit"
        ? `Committed: ${res.created} created, ${res.updated} updated, ${res.verifications} provenance records written.`
        : "Batch discarded.");
      await reload(); onChanged();
    } catch (e: any) { setMsg(e?.message ?? "Failed."); }
    finally { setBusy(false); }
  };

  if (loading) return <p className="p-3 text-xs text-slate-500">Loading…</p>;
  if (error) return <p className="p-3 text-xs text-rose-700">{error}</p>;

  const staged = data?.status === "STAGED";
  const approved = (data?.rows ?? []).filter((r: Dict) => r.decision === "APPROVE").length;

  return (
    <div className="border-t border-slate-200 p-3">
      {data?.source_url && (
        <p className="mb-2 text-[10px] text-slate-500">
          Batch source: <span className="font-mono">{data.source_url}</span> · {data.source_type}
        </p>
      )}
      <div className="max-h-96 overflow-auto rounded-lg ring-1 ring-slate-200">
        <table className="w-full text-left text-[11px]">
          <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
            <tr><th className="p-1.5">#</th><th>Key</th><th>Action</th><th>Changes</th><th>Decision</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {(data?.rows ?? []).map((r: Dict) => (
              <tr key={r.id} className={r.action === "CONFLICT" ? "bg-amber-50/50" : ""}>
                <td className="p-1.5 text-slate-400">{r.line_number}</td>
                <td className="font-mono text-slate-700">{r.natural_key ?? "—"}</td>
                <td><span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${ACTION_TONE[r.action]}`}>{r.action}</span></td>
                <td className="py-1.5">
                  {r.errors?.length > 0 && (
                    <p className="text-rose-700">{r.errors.join("; ")}</p>
                  )}
                  {Object.entries(r.diff ?? {}).slice(0, 4).map(([field, c]: [string, any]) => (
                    <p key={field} className={c.conflict ? "text-amber-900" : "text-slate-600"}>
                      <span className="font-semibold">{field}</span>:{" "}
                      <span className="line-through opacity-60">{String(c.current ?? "—").slice(0, 28)}</span>
                      {" → "}
                      <span className="font-semibold">{String(c.incoming ?? "—").slice(0, 28)}</span>
                      {c.conflict && <span className="ml-1 font-bold">⚠ contradicts verified source</span>}
                    </p>
                  ))}
                  {Object.keys(r.diff ?? {}).length > 4 && (
                    <p className="text-slate-400">+{Object.keys(r.diff).length - 4} more fields</p>
                  )}
                  {r.message && <p className="text-emerald-700">{r.message}</p>}
                </td>
                <td>
                  {staged && !r.errors?.length ? (
                    <div className="flex gap-1">
                      <button type="button" disabled={busy} onClick={() => decide(r.id, "APPROVE")}
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${r.decision === "APPROVE"
                          ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
                        approve
                      </button>
                      <button type="button" disabled={busy} onClick={() => decide(r.id, "SKIP")}
                        className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${r.decision === "SKIP"
                          ? "bg-slate-700 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
                        skip
                      </button>
                    </div>
                  ) : (
                    <span className="text-[10px] text-slate-400">{r.applied ? "applied" : r.decision}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {staged && (
          <>
            <Btn small onClick={() => act("commit")} disabled={busy || approved === 0}>
              Commit {approved} approved row(s)
            </Btn>
            <Btn small tone="danger" onClick={() => act("discard")} disabled={busy}>Discard</Btn>
          </>
        )}
        {msg && <span className="text-[11px] font-semibold text-brand-800">{msg}</span>}
      </div>
    </div>
  );
}

/* ==================================================== verification queue */
const STATUS_TONE: Dict = {
  VERIFIED: "bg-emerald-100 text-emerald-800",
  NEEDS_REVIEW: "bg-amber-100 text-amber-900",
  CONFLICTING: "bg-rose-100 text-rose-800",
  OUTDATED: "bg-orange-100 text-orange-800",
  UNVERIFIED: "bg-slate-100 text-slate-600",
};

function VerificationQueue() {
  const [filter, setFilter] = useState("");
  const path = `/verification/queue${filter ? `?status=${filter}` : ""}`;
  const { data, error, loading, reload } = useResource<Dict>(path);
  const [busy, setBusy] = useState(0);

  const resolve = async (id: number, status: string) => {
    setBusy(id);
    try {
      await adminFetch(`/verification/${id}/resolve`, {
        method: "POST", body: JSON.stringify({ status }),
      });
      await reload();
    } finally { setBusy(0); }
  };

  return (
    <Panel
      title="Verification queue"
      hint="Field-level provenance. Resolving a field also moves the parent record's confidence state."
      action={
        <select className="rounded-lg border border-slate-300 px-2 py-1 text-xs"
          value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All statuses</option>
          {Object.keys(STATUS_TONE).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      }
    >
      {error && <p className="text-xs text-rose-700">{error}</p>}
      {loading && <p className="py-6 text-center text-xs text-slate-500">Loading…</p>}

      {data && (
        <>
          <div className="mb-3 flex flex-wrap gap-1.5">
            {Object.entries(data.counts ?? {}).map(([k, v]) => (
              <span key={k} className={`rounded px-2 py-0.5 text-[10px] font-bold ${STATUS_TONE[k] ?? "bg-slate-100"}`}>
                {k} {v as number}
              </span>
            ))}
            {Object.keys(data.counts ?? {}).length === 0 && (
              <span className="text-[11px] text-slate-500">
                Nothing in the queue — provenance records appear here as data is imported or edited.
              </span>
            )}
          </div>

          <div className="max-h-96 overflow-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
                <tr><th className="p-1.5">Record</th><th>Field</th><th>Value</th><th>Source</th><th>Status</th><th /></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {(data.items ?? []).map((r: Dict) => (
                  <tr key={r.id}>
                    <td className="p-1.5 text-slate-600">{r.entity_type} #{r.entity_id}</td>
                    <td className="font-semibold text-slate-800">{r.field_name}</td>
                    <td className="max-w-[16rem] truncate text-slate-700">{r.field_value}</td>
                    <td className="max-w-[14rem] truncate">
                      {r.source_url
                        ? <a href={r.source_url} target="_blank" rel="noopener noreferrer nofollow"
                            className="text-brand-800 hover:underline">{r.source_url}</a>
                        : <span className="text-rose-600">none</span>}
                      {r.conflict_with && (
                        <p className="text-[10px] text-rose-700">
                          was {String(r.conflict_with.previous_value).slice(0, 30)} (from {r.conflict_with.previous_source ?? "?"})
                        </p>
                      )}
                    </td>
                    <td><span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${STATUS_TONE[r.status] ?? ""}`}>{r.status}</span></td>
                    <td>
                      <div className="flex gap-1">
                        <button type="button" disabled={busy === r.id} onClick={() => resolve(r.id, "VERIFIED")}
                          className="rounded bg-emerald-600 px-1.5 py-0.5 text-[10px] font-bold text-white hover:bg-emerald-500">
                          verify
                        </button>
                        <button type="button" disabled={busy === r.id} onClick={() => resolve(r.id, "OUTDATED")}
                          className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-bold text-slate-700 hover:bg-slate-300">
                          outdated
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Panel>
  );
}
