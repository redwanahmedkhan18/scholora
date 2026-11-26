"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AuthError, adminFetch } from "@/lib/admin";

type Dict = Record<string, any>;

/* ------------------------------------------------------------- primitives */
function useResource<T = Dict>(path: string, enabled = true) {
  const queryClient = useQueryClient();
  const query = useQuery<T, Error>({
    queryKey: ["admin", path],
    queryFn: () => adminFetch<T>(path),
    enabled,
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

function Loading({ error, loading }: { error: string; loading: boolean }) {
  if (error) return <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">{error}</p>;
  if (loading) return <p className="py-10 text-center text-sm text-slate-500">Loading…</p>;
  return null;
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

function Flash({ result }: { result: Dict | null }) {
  if (!result) return null;
  const ok = result.ok ?? !result.error;
  return (
    <p className={`mt-2 rounded-lg px-3 py-2 text-xs ring-1 ${ok
      ? "bg-emerald-50 text-emerald-900 ring-emerald-200"
      : "bg-rose-50 text-rose-900 ring-rose-200"}`}>
      <span className="font-bold">{ok ? "Connected" : "Failed"}</span>
      {result.detail ? ` — ${result.detail}` : ""}
    </p>
  );
}

/* ============================================================ credentials */
/**
 * Credentials, the embedding provider and the retrieval weights used to be `.env` values:
 * changing one meant an engineer, a deploy and a restart. They are now database-backed
 * overrides that take effect on the next request. Secrets are encrypted at rest and the
 * server only ever returns a masked preview, so this screen cannot leak a token it holds.
 */
export function PlatformConfig() {
  const { data, error, loading, reload } = useResource<Dict>("/config");
  const [draft, setDraft] = useState<Dict>({});
  const [saving, setSaving] = useState(false);
  const [tests, setTests] = useState<Dict>({});
  const [busy, setBusy] = useState("");
  const [saved, setSaved] = useState("");

  const dirty = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true); setSaved("");
    try {
      const res = await adminFetch<Dict>("/config", {
        method: "PUT",
        body: JSON.stringify({ values: draft }),
      });
      setDraft({});
      setSaved(
        `Saved ${res.changed?.length ?? 0} setting(s)` +
        (res.cleared?.length ? `, cleared ${res.cleared.length}` : "") +
        (res.reindex_recommended ? ". A reindex is recommended — see Embeddings below." : "."),
      );
      await reload();
    } catch (e: any) {
      setSaved(e?.message ?? "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const runTest = async (target: string) => {
    setBusy(target);
    try {
      const res = await adminFetch<Dict>(`/config/test/${target}`, { method: "POST" });
      setTests((t) => ({ ...t, [target]: res }));
    } catch (e: any) {
      setTests((t) => ({ ...t, [target]: { ok: false, detail: e?.message ?? "Test failed" } }));
    } finally {
      setBusy("");
    }
  };

  // Which "test connection" button belongs under which group.
  const TESTS: Dict = {
    whatsapp: [["whatsapp", "Test WhatsApp"]],
    meta: [["meta_capi", "Test Conversions API"], ["facebook_page", "Test page token"]],
    // Verifies the connection and the App Password without sending anything (no body).
    // To prove the last mile, run `python -m app.mgmt_admin --send-test-email you@example.org`.
    email: [["smtp", "Test SMTP"]],
    llm: [["grok", "Test Grok"], ["mistral", "Test Mistral"], ["gemini", "Test Gemini"]],
    embeddings: [["embeddings", "Test embeddings"]],
  };

  if (loading || error) return <Loading error={error} loading={loading} />;

  return (
    <div className="space-y-5">
      <Panel
        title="Credentials and runtime settings"
        hint="Stored in the database, encrypted where secret, and applied without a redeploy. Leave a field blank to fall back to the environment variable."
        action={
          <div className="flex items-center gap-2">
            {saved && <span className="text-[11px] text-slate-500">{saved}</span>}
            <Btn onClick={save} disabled={!dirty || saving}>
              {saving ? "Saving…" : dirty ? `Save ${Object.keys(draft).length} change(s)` : "Saved"}
            </Btn>
          </div>
        }
      >
        <p className="mb-4 text-xs text-slate-600">
          <span className="font-semibold">{data?.configured}</span> of {data?.total} settings configured.
        </p>

        <div className="space-y-6">
          {(data?.groups ?? []).map((group: Dict) => (
            <div key={group.key} className="rounded-xl border border-slate-200 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-bold uppercase tracking-wide text-slate-700">{group.label}</h3>
                <div className="flex flex-wrap gap-1.5">
                  {(TESTS[group.key] ?? []).map(([target, label]: string[]) => (
                    <Btn key={target} tone="ghost" small onClick={() => runTest(target)} disabled={busy === target}>
                      {busy === target ? "Testing…" : label}
                    </Btn>
                  ))}
                </div>
              </div>

              {(TESTS[group.key] ?? []).map(([target]: string[]) => (
                <Flash key={target} result={tests[target] ?? null} />
              ))}

              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {group.items.map((item: Dict) => {
                  const pending = draft[item.key];
                  const value = pending !== undefined
                    ? pending
                    : item.secret ? "" : (item.value ?? "");
                  return (
                    <label key={item.key} className="block">
                      <span className="flex items-center justify-between gap-2 text-[11px] font-semibold text-slate-700">
                        <span>{item.label}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${
                          item.origin === "database" ? "bg-brand-50 text-brand-800"
                            : item.origin === "environment" ? "bg-slate-100 text-slate-600"
                              : "bg-amber-50 text-amber-800"}`}>
                          {item.origin}
                        </span>
                      </span>

                      {item.kind === "bool" ? (
                        <select
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
                          value={String(value)}
                          onChange={(e) => setDraft((d) => ({ ...d, [item.key]: e.target.value }))}>
                          <option value="true">Enabled</option>
                          <option value="false">Disabled</option>
                        </select>
                      ) : (
                        <input
                          className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 font-mono text-xs"
                          type={item.secret ? "password" : item.kind === "int" || item.kind === "float" ? "number" : "text"}
                          step={item.kind === "float" ? "0.01" : undefined}
                          min={item.minimum ?? undefined}
                          max={item.maximum ?? undefined}
                          placeholder={item.secret && item.has_value ? item.value : "not set"}
                          value={value}
                          onChange={(e) => setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
                        />
                      )}
                      {item.help && <span className="mt-1 block text-[10px] leading-snug text-slate-500">{item.help}</span>}
                      {item.requires_reindex && (
                        <span className="mt-0.5 block text-[10px] font-semibold text-amber-700">
                          Changing this invalidates stored vectors — reindex afterwards.
                        </span>
                      )}
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <EmbeddingsPanel />
      <RetrievalTuner />
    </div>
  );
}

/* ============================================================= embeddings */
function EmbeddingsPanel() {
  const { data, error, loading, reload } = useResource<Dict>("/embeddings/status");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const reindex = async (background: boolean) => {
    setBusy(true); setMsg("");
    try {
      const res = await adminFetch<Dict>(`/embeddings/reindex?background=${background}`, { method: "POST" });
      setMsg(res.detail ?? (res.result ? `Reindexed ${res.result.embedded} chunks.` : "Done."));
      await reload();
    } catch (e: any) {
      setMsg(e?.message ?? "Reindex failed.");
    } finally {
      setBusy(false);
    }
  };

  if (loading || error) return <Loading error={error} loading={loading} />;
  const drift = data?.drift ?? {};

  return (
    <Panel
      title="Embeddings"
      hint="Vectors from different models are not comparable, so switching provider silently degrades every search until the corpus is rebuilt. This panel makes that visible."
      action={
        <div className="flex gap-2">
          <Btn tone="ghost" onClick={() => reindex(false)} disabled={busy}>Reindex now</Btn>
          <Btn onClick={() => reindex(true)} disabled={busy}>{busy ? "Working…" : "Queue reindex"}</Btn>
        </div>
      }
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
          <p className="text-[10px] font-bold uppercase tracking-wide text-slate-500">Active model</p>
          <p className="mt-0.5 font-mono text-xs font-bold text-slate-900">{drift.current_model}</p>
          <p className="text-[10px] text-slate-500">{drift.current_dim} dimensions</p>
        </div>
        <div className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
          <p className="text-[10px] font-bold uppercase tracking-wide text-slate-500">Corpus</p>
          <p className="mt-0.5 text-xl font-black text-slate-900">{drift.total_chunks}</p>
          <p className="text-[10px] text-slate-500">chunks indexed</p>
        </div>
        <div className={`rounded-xl p-3 ring-1 ${drift.in_sync
          ? "bg-emerald-50 ring-emerald-200" : "bg-amber-50 ring-amber-200"}`}>
          <p className="text-[10px] font-bold uppercase tracking-wide opacity-70">Drift</p>
          <p className="mt-0.5 text-xl font-black">{drift.stale_chunks}</p>
          <p className="text-[10px] opacity-80">{drift.in_sync ? "all vectors current" : "stale vectors"}</p>
        </div>
      </div>

      <p className={`mt-3 rounded-lg px-3 py-2 text-xs ring-1 ${drift.reindex_required
        ? "bg-amber-50 text-amber-900 ring-amber-200" : "bg-slate-50 text-slate-700 ring-slate-200"}`}>
        {data?.advice}
      </p>
      {msg && <p className="mt-2 text-xs font-semibold text-brand-800">{msg}</p>}

      <table className="mt-4 w-full text-left text-xs">
        <thead className="text-[10px] uppercase tracking-wide text-slate-500">
          <tr><th className="py-1.5">Provider</th><th>Model</th><th>Priority</th><th>Key set</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {(data?.providers ?? []).map((p: Dict) => (
            <tr key={p.key}>
              <td className="py-1.5 font-semibold text-slate-800">{p.label}</td>
              <td className="font-mono text-[11px] text-slate-600">{p.model}</td>
              <td className="text-slate-600">{p.priority}</td>
              <td>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${p.configured
                  ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-500"}`}>
                  {p.configured ? "yes" : "no"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(drift.by_model ?? {}).length > 1 && (
        <p className="mt-2 text-[11px] text-slate-500">
          Vectors by model: {Object.entries(drift.by_model).map(([m, n]) => `${m} (${n})`).join(", ")}
        </p>
      )}
    </Panel>
  );
}

/* ========================================================= retrieval tuner */
const DEFAULT_PROBES = [
  "scholarships for computer science in the UK",
  "IELTS requirement for a master's in Canada",
  "cheapest country to study engineering",
];

function RetrievalTuner() {
  const [query, setQuery] = useState(DEFAULT_PROBES[0]);
  const [dense, setDense] = useState(0.65);
  const [floor, setFloor] = useState(0.1);
  const [preview, setPreview] = useState<Dict | null>(null);
  const [sweep, setSweep] = useState<Dict | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");

  const run = async () => {
    setBusy("preview"); setMsg("");
    try {
      setPreview(await adminFetch<Dict>("/retrieval/preview", {
        method: "POST",
        body: JSON.stringify({ query, dense_weight: dense, min_score: floor, limit: 8 }),
      }));
    } catch (e: any) { setMsg(e?.message ?? "Preview failed."); }
    finally { setBusy(""); }
  };

  const runSweep = async () => {
    setBusy("sweep"); setMsg("");
    try {
      setSweep(await adminFetch<Dict>("/retrieval/sweep", {
        method: "POST", body: JSON.stringify({ queries: DEFAULT_PROBES }),
      }));
    } catch (e: any) { setMsg(e?.message ?? "Sweep failed."); }
    finally { setBusy(""); }
  };

  const apply = async () => {
    setBusy("apply"); setMsg("");
    try {
      await adminFetch<Dict>("/config", {
        method: "PUT",
        body: JSON.stringify({ values: { retrieval_dense_weight: String(dense), retrieval_min_score: String(floor) } }),
      });
      setMsg(`Applied dense weight ${dense} and floor ${floor}.`);
      await run();
    } catch (e: any) { setMsg(e?.message ?? "Could not apply."); }
    finally { setBusy(""); }
  };

  const maxSweep = useMemo(
    () => Math.max(1, ...((sweep?.grid ?? []).map((r: Dict) => r.mean_top_score || 0))),
    [sweep],
  );

  return (
    <Panel
      title="Retrieval tuning"
      hint="The defaults were calibrated against the local vectoriser on demo content. A hosted embedding model scores differently, so compare real results before changing anything."
    >
      <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto_auto] sm:items-end">
        <label className="block">
          <span className="text-[11px] font-semibold text-slate-700">Test query</span>
          <input className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs"
            value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <label className="block">
          <span className="text-[11px] font-semibold text-slate-700">Dense weight {dense.toFixed(2)}</span>
          <input type="range" min={0} max={1} step={0.05} value={dense}
            onChange={(e) => setDense(Number(e.target.value))} className="mt-2 w-36 accent-brand-800" />
        </label>
        <label className="block">
          <span className="text-[11px] font-semibold text-slate-700">Floor {floor.toFixed(2)}</span>
          <input type="range" min={0} max={0.5} step={0.01} value={floor}
            onChange={(e) => setFloor(Number(e.target.value))} className="mt-2 w-28 accent-brand-800" />
        </label>
        <div className="flex gap-2">
          <Btn onClick={run} disabled={busy !== ""}>{busy === "preview" ? "Running…" : "Compare"}</Btn>
          <Btn tone="ghost" onClick={runSweep} disabled={busy !== ""}>{busy === "sweep" ? "…" : "Sweep"}</Btn>
        </div>
      </div>
      {msg && <p className="mt-2 text-xs font-semibold text-brand-800">{msg}</p>}

      {preview && (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-2 text-[11px]">
            <span className={`rounded-full px-2 py-0.5 font-bold ${preview.changed
              ? "bg-amber-100 text-amber-900" : "bg-slate-100 text-slate-600"}`}>
              {preview.changed ? "Ranking changes" : "No change"}
            </span>
            {preview.entered?.length > 0 && <span className="text-emerald-700">+{preview.entered.length} entered</span>}
            {preview.left?.length > 0 && <span className="text-rose-700">−{preview.left.length} dropped</span>}
            <span className="font-mono text-slate-500">{preview.embedding_model}</span>
            <Btn small tone="ghost" onClick={apply} disabled={busy !== ""}>Apply these weights</Btn>
          </div>

          <div className="mt-3 grid gap-4 lg:grid-cols-2">
            {(["current", "candidate"] as const).map((side) => (
              <div key={side}>
                <p className="mb-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-500">
                  {side} · dense {preview[side].weights.dense_weight} · floor {preview[side].weights.min_score}
                </p>
                <ol className="space-y-1">
                  {preview[side].results.map((r: Dict) => (
                    <li key={`${side}-${r.rank}`} className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px]">
                      <span className="truncate text-slate-800">{r.rank}. {r.title}</span>
                      <span className="shrink-0 font-mono text-slate-500">
                        {r.score.toFixed(3)} <span className="text-slate-400">d{r.dense.toFixed(2)}/l{r.lexical.toFixed(2)}</span>
                      </span>
                    </li>
                  ))}
                  {preview[side].results.length === 0 && (
                    <li className="rounded-lg bg-rose-50 px-2.5 py-1.5 text-[11px] text-rose-800">No results</li>
                  )}
                </ol>
              </div>
            ))}
          </div>
        </>
      )}

      {sweep && (
        <div className="mt-5 rounded-xl border border-slate-200 p-3">
          <p className="text-[11px] font-bold uppercase tracking-wide text-slate-600">
            Dense-weight sweep over {sweep.queries.length} queries
          </p>
          <div className="mt-2 space-y-1">
            {sweep.grid.map((r: Dict) => (
              <div key={r.dense_weight} className="flex items-center gap-2 text-[11px]">
                <span className={`w-8 font-mono ${r.dense_weight === sweep.current_dense_weight
                  ? "font-bold text-brand-800" : "text-slate-500"}`}>{r.dense_weight.toFixed(1)}</span>
                <div className="h-3 flex-1 overflow-hidden rounded bg-slate-100">
                  <div className="h-full rounded bg-brand-700"
                    style={{ width: `${(r.mean_top_score / maxSweep) * 100}%` }} />
                </div>
                <span className="w-28 text-right font-mono text-slate-500">
                  {r.coverage_pct}% · {r.mean_top_score.toFixed(3)}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] leading-snug text-slate-500">{sweep.caveat}</p>
        </div>
      )}
    </Panel>
  );
}
