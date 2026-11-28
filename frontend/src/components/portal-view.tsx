"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { money } from "@/lib/format";
import {
  DOC_LABELS, STATUS_LABELS, portalFetch, statusTone,
} from "@/lib/portal";
import { usePortalSession } from "@/lib/use-portal-session";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

const TABS = ["Overview", "Applications", "Documents", "Shortlist", "Updates"] as const;
type Tab = typeof TABS[number];

export default function PortalView({ token, consumeToken = false }:
  { token: string; consumeToken?: boolean }) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("Overview");
  const onTokenConsumed = useCallback(() => router.replace("/my"), [router]);
  const onSignedOut = useCallback(() => router.replace("/"), [router]);
  const { data, error, load, signOut } = usePortalSession(token, {
    consumeToken, onTokenConsumed, onSignedOut,
  });

  if (error) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-bold text-slate-900">We could not open that portal</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">{error}</p>
        <Link href="/profile-assessment" className="mt-5 inline-block rounded-xl bg-brand-800 px-5 py-3 text-sm font-bold text-white">
          Run the free profile assessment
        </Link>
      </div>
    );
  }

  if (!data) return <p className="py-16 text-center text-sm text-slate-500">Loading your portal…</p>;

  const c = data.counts ?? {};

  return (
    <div className="space-y-6">
      <header className="card overflow-hidden">
        <div className="bg-brand-900 px-5 py-6 text-white sm:px-7">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-accent-400">Student portal</p>
          <h1 className="mt-1 text-2xl font-black sm:text-3xl">{data.profile.full_name}</h1>
          <p className="mt-1 text-sm text-brand-100">
            {[data.profile.target_degree_level, data.profile.target_discipline, data.profile.target_intake]
              .filter(Boolean).join(" · ") || "Tell your counselor your target intake to sharpen your plan."}
          </p>
          <button type="button" onClick={signOut}
            className="mt-3 rounded-lg bg-white/10 px-3 py-1.5 text-xs font-bold text-white ring-1 ring-white/20 hover:bg-white/20">
            Sign out
          </button>
        </div>
        <div className="grid grid-cols-2 divide-x divide-slate-200 border-t border-slate-200 sm:grid-cols-4">
          <Kpi value={c.applications} label="Applications" />
          <Kpi value={`${c.checklist_complete}/${c.checklist_total}`} label="Core documents" />
          <Kpi value={c.shortlist} label="Shortlisted" />
          <Kpi value={c.unread_notifications} label="New updates" />
        </div>
      </header>

      <nav className="flex gap-1.5 overflow-x-auto pb-1" aria-label="Portal sections">
        {TABS.map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={`whitespace-nowrap rounded-full px-4 py-2 text-sm font-bold transition ${tab === t ? "bg-brand-800 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"}`}>
            {t}
            {t === "Updates" && !!c.unread_notifications && (
              <span className="ml-1.5 rounded-full bg-accent-500 px-1.5 text-[10px] font-black text-brand-950">{c.unread_notifications}</span>
            )}
          </button>
        ))}
      </nav>

      {tab === "Overview" && <Overview data={data} onJump={setTab} />}
      {tab === "Applications" && <Applications data={data} token={token} reload={load} />}
      {tab === "Documents" && <Documents data={data} token={token} reload={load} />}
      {tab === "Shortlist" && <Shortlist data={data} token={token} reload={load} />}
      {tab === "Updates" && <Updates data={data} token={token} reload={load} />}

      <p className="rounded-xl bg-slate-100 p-4 text-[11px] leading-relaxed text-slate-600">
        <strong>Keep this link private.</strong> Anyone with it can see your profile, documents and
        applications. Document downloads use links that expire within minutes and are never indexed.
        Nothing here is an offer, scholarship or visa guarantee.
      </p>
    </div>
  );
}

function Kpi({ value, label }: { value: any; label: string }) {
  return (
    <div className="px-4 py-4 text-center">
      <p className="text-2xl font-black text-brand-900">{value ?? 0}</p>
      <p className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  );
}

/* --------------------------------------------------------------- overview */
function Overview({ data, onJump }: { data: Dict; onJump: (t: Tab) => void }) {
  const p = data.profile;
  const missing = (data.document_checklist ?? []).filter((d: Dict) => d.required && !d.provided);
  const next = (data.applications ?? [])[0];

  return (
    <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
      <div className="space-y-5">
        {!!missing.length && (
          <div className="card border-l-4 border-l-amber-400 p-5">
            <p className="text-sm font-bold text-slate-900">
              {missing.length} core document{missing.length > 1 ? "s" : ""} still needed
            </p>
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {missing.map((d: Dict) => (
                <li key={d.doc_type} className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-900 ring-1 ring-amber-200">
                  {DOC_LABELS[d.doc_type] ?? d.doc_type}
                </li>
              ))}
            </ul>
            <button type="button" onClick={() => onJump("Documents")}
              className="mt-3 rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">Upload documents</button>
          </div>
        )}

        {next ? (
          <div className="card p-5">
            <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Latest application</p>
            <p className="mt-1 text-base font-bold text-slate-900">{next.program?.name}</p>
            <p className="text-sm text-slate-600">{next.university?.name}</p>
            <ProgressBar application={next} />
            <button type="button" onClick={() => onJump("Applications")}
              className="mt-3 text-sm font-bold text-brand-800 underline">See all applications</button>
          </div>
        ) : (
          <div className="card p-5">
            <p className="text-sm font-bold text-slate-900">No applications started yet</p>
            <p className="mt-1 text-sm text-slate-600">
              Open any programme page and choose “Start an application” — or ask a counselor to set it up with you.
            </p>
            <Link href="/programs" className="mt-3 inline-block rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">
              Browse programmes
            </Link>
          </div>
        )}

        <div className="card p-5">
          <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Your academic profile</p>
          <dl className="mt-3 grid gap-x-6 gap-y-2.5 text-sm sm:grid-cols-2">
            <Row label="Highest qualification" value={p.highest_qualification} />
            <Row label="Major" value={p.major} />
            <Row label="CGPA" value={p.cgpa ? `${p.cgpa} / ${p.cgpa_scale ?? 4}` : null} />
            <Row label="IELTS overall" value={p.ielts_overall} />
            <Row label="Target intake" value={p.target_intake} />
            <Row label="Budget / year" value={p.budget_per_year ? money(p.budget_per_year, p.budget_currency ?? "USD") : null} />
            <Row label="Preferred countries" value={(p.preferred_countries ?? []).join(", ")} />
            <Row label="Research interests" value={(p.research_interests ?? []).join(", ")} />
          </dl>
          <Link href="/profile-assessment" className="mt-4 inline-block text-sm font-bold text-brand-800 underline">
            Update my profile and re-run matching
          </Link>
        </div>
      </div>

      <div className="space-y-5">
        <div className="card p-5">
          <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Your brochures</p>
          {(data.brochures ?? []).length === 0 ? (
            <p className="mt-2 text-sm text-slate-600">
              Generate a personalised, source-backed brochure from any programme page.
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.brochures.map((b: Dict) => (
                <li key={b.token}>
                  <Link href={b.url} className="block rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200 hover:bg-slate-100">
                    <span className="block text-sm font-bold text-slate-800">{b.degree_level} brochure · v{b.version}</span>
                    <span className="block text-[11px] text-slate-500">
                      {b.target_intake ?? "No intake set"} · {String(b.created_at).slice(0, 10)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card p-5">
          <p className="text-xs font-bold uppercase tracking-wide text-slate-500">Need a person?</p>
          <div className="mt-3 space-y-2">
            <Link href="/appointments" className="block rounded-xl bg-brand-800 px-4 py-2.5 text-center text-sm font-bold text-white">
              Book a counselor
            </Link>
            <Link href="/contact" className="block rounded-xl bg-white px-4 py-2.5 text-center text-sm font-bold text-slate-700 ring-1 ring-slate-300">
              Contact the Uttara office
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="font-semibold text-slate-800">{value || <span className="text-slate-400">Not provided</span>}</dd>
    </div>
  );
}

function ProgressBar({ application }: { application: Dict }) {
  const pct = application.progress_percent ?? 0;
  const terminal = ["WITHDRAWN", "REJECTED"].includes(application.status);
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between text-[11px] font-semibold">
        <span className={`rounded-full px-2 py-0.5 ring-1 ${statusTone(application.status)}`}>
          {STATUS_LABELS[application.status] ?? application.status}
        </span>
        <span className="text-slate-500">{application.reference}</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full rounded-full ${terminal ? "bg-slate-400" : "bg-brand-700"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- applications */
function Applications({ data, token, reload }: { data: Dict; token: string; reload: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);

  const withdraw = async (id: number) => {
    if (!confirm("Withdraw this application? Your counselor will be notified.")) return;
    setBusy(id);
    try {
      await portalFetch(`/portal/${token}/applications/${id}`, { method: "DELETE" });
      track("application_withdraw", { id });
      reload();
    } finally { setBusy(null); }
  };

  if (!data.applications?.length) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-bold text-slate-900">No applications yet</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
          When you start an application from a programme page it appears here with every status change,
          so you always know exactly where it stands.
        </p>
        <Link href="/programs" className="mt-5 inline-block rounded-xl bg-brand-800 px-5 py-3 text-sm font-bold text-white">
          Find a programme
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {data.applications.map((a: Dict) => (
        <article key={a.id} className="card p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-base font-bold text-slate-900">{a.program?.name}</h2>
              <p className="text-sm text-slate-600">
                {a.university?.name}{a.university?.city ? `, ${a.university.city}` : ""} · {a.intake_label ?? "intake to confirm"}
              </p>
            </div>
            {a.program?.tuition_annual && (
              <p className="text-sm font-bold text-brand-900">
                {money(a.program.tuition_annual, a.program.currency ?? "USD")}<span className="text-xs font-normal text-slate-500">/yr</span>
              </p>
            )}
          </div>

          <ProgressBar application={a} />

          {!!a.history?.length && (
            <details className="mt-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200">
              <summary className="cursor-pointer text-xs font-bold text-slate-700">
                Status history ({a.history.length})
              </summary>
              <ol className="mt-2 space-y-2">
                {a.history.slice().reverse().map((h: Dict, i: number) => (
                  <li key={i} className="border-l-2 border-slate-300 pl-3 text-xs">
                    <p className="font-semibold text-slate-800">
                      {STATUS_LABELS[h.to] ?? h.to}
                      {h.from && <span className="font-normal text-slate-500"> — from {STATUS_LABELS[h.from] ?? h.from}</span>}
                    </p>
                    {h.note && <p className="text-slate-600">{h.note}</p>}
                    <p className="text-slate-400">{String(h.at).slice(0, 16).replace("T", " ")}</p>
                  </li>
                ))}
              </ol>
            </details>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {a.program?.slug && (
              <Link href={`/programs/${a.program.slug}`} className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50">
                View programme
              </Link>
            )}
            {!["WITHDRAWN", "REJECTED", "COMPLETED"].includes(a.status) && (
              <button type="button" onClick={() => withdraw(a.id)} disabled={busy === a.id}
                className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50 disabled:opacity-50">
                {busy === a.id ? "Withdrawing…" : "Withdraw"}
              </button>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------- documents */
function Documents({ data, token, reload }: { data: Dict; token: string; reload: () => void }) {
  const [docType, setDocType] = useState("PASSPORT");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) { setErr("Choose a file first."); return; }
    setBusy(true); setErr(""); setMsg("");
    try {
      const form = new FormData();
      form.append("doc_type", docType);
      form.append("file", file);
      await portalFetch(`/portal/${token}/documents`, { method: "POST", body: form });
      setMsg(`${DOC_LABELS[docType] ?? docType} uploaded. A counselor will review it.`);
      if (fileRef.current) fileRef.current.value = "";
      track("document_upload", { doc_type: docType });
      reload();
    } catch (e: any) {
      setErr(e?.message ?? "Upload failed.");
    } finally { setBusy(false); }
  };

  const remove = async (id: number) => {
    if (!confirm("Delete this document?")) return;
    try { await portalFetch(`/portal/${token}/documents/${id}`, { method: "DELETE" }); reload(); }
    catch (e: any) { setErr(e?.message ?? "Could not delete."); }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
      <div className="space-y-4">
        <div className="card p-5">
          <h2 className="text-sm font-bold text-slate-900">Your documents</h2>
          {!data.documents?.length ? (
            <p className="mt-2 text-sm text-slate-600">Nothing uploaded yet. Start with your passport and transcripts.</p>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100">
              {data.documents.map((d: Dict) => (
                <li key={d.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-bold text-slate-800">{DOC_LABELS[d.doc_type] ?? d.doc_type}</p>
                    <p className="truncate text-[11px] text-slate-500">
                      {d.original_filename} · {Math.round((d.size_bytes ?? 0) / 1024)} KB · {String(d.uploaded_at).slice(0, 10)}
                    </p>
                    {d.review_note && <p className="mt-1 text-[11px] text-slate-600">“{d.review_note}”</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ring-1 ${
                      d.status === "ACCEPTED" ? "bg-emerald-100 text-emerald-800 ring-emerald-200"
                      : d.status === "REJECTED" ? "bg-rose-100 text-rose-800 ring-rose-200"
                      : "bg-slate-100 text-slate-700 ring-slate-200"}`}>
                      {d.status.replace(/_/g, " ")}
                    </span>
                    <a href={d.download_url} target="_blank" rel="noopener noreferrer"
                      className="rounded-lg bg-white px-3 py-1.5 text-xs font-bold text-brand-800 ring-1 ring-slate-300 hover:bg-slate-50">
                      View
                    </a>
                    {d.status !== "ACCEPTED" && (
                      <button type="button" onClick={() => remove(d.id)}
                        className="rounded-lg px-2 py-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50">✕</button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card p-5">
          <h2 className="text-sm font-bold text-slate-900">Checklist</h2>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {(data.document_checklist ?? []).map((d: Dict) => (
              <li key={d.doc_type} className="flex items-center gap-2 text-sm">
                <span aria-hidden className={d.provided ? "text-emerald-600" : "text-slate-300"}>{d.provided ? "●" : "○"}</span>
                <span className={d.provided ? "text-slate-700" : "text-slate-500"}>{DOC_LABELS[d.doc_type] ?? d.doc_type}</span>
                {d.required && !d.provided && <span className="text-[10px] font-bold uppercase text-amber-700">required</span>}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <form onSubmit={upload} className="card h-fit p-5">
        <h2 className="text-sm font-bold text-slate-900">Upload a document</h2>
        <label className="mt-3 block">
          <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Type</span>
          <select value={docType} onChange={(e) => setDocType(e.target.value)}
            className="w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm">
            {(data.document_types ?? []).map((t: string) => (
              <option key={t} value={t}>{DOC_LABELS[t] ?? t}</option>
            ))}
          </select>
        </label>
        <label className="mt-3 block">
          <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">File</span>
          <input ref={fileRef} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx"
            className="w-full rounded-xl border border-slate-300 px-3 py-2 text-xs file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-xs file:font-bold file:text-brand-800" />
        </label>

        {err && <p className="mt-3 rounded-lg bg-rose-50 p-2.5 text-[11px] text-rose-800 ring-1 ring-rose-200">{err}</p>}
        {msg && <p className="mt-3 rounded-lg bg-emerald-50 p-2.5 text-[11px] text-emerald-800 ring-1 ring-emerald-200">{msg}</p>}

        <button type="submit" disabled={busy}
          className="mt-4 w-full rounded-xl bg-brand-800 px-4 py-3 text-sm font-bold text-white disabled:opacity-60">
          {busy ? "Uploading…" : "Upload"}
        </button>
        <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
          PDF, JPG, PNG, WEBP, HEIC, DOC or DOCX up to 15 MB. Files are stored privately — they are never
          public, and every view uses a link that expires within minutes.
        </p>
      </form>
    </div>
  );
}

/* --------------------------------------------------------------- shortlist */
function Shortlist({ data, token, reload }: { data: Dict; token: string; reload: () => void }) {
  const remove = async (id: number) => {
    await portalFetch(`/portal/${token}/shortlist/${id}`, { method: "DELETE" });
    reload();
  };

  if (!data.shortlist?.length) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-bold text-slate-900">Your saved list is empty</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
          Tap “Add to shortlist” on any programme, university or scholarship. Saved items follow you
          across devices once they are in your portal.
        </p>
        <Link href="/programs" className="mt-5 inline-block rounded-xl bg-brand-800 px-5 py-3 text-sm font-bold text-white">
          Browse programmes
        </Link>
      </div>
    );
  }

  const href = (s: Dict) =>
    s.meta?.href ??
    (s.entity_type === "program" ? `/programs/${s.slug}`
      : s.entity_type === "scholarship" ? `/scholarships/${s.slug}` : `/universities`);

  return (
    <div className="space-y-3">
      {data.shortlist.map((s: Dict) => (
        <div key={s.id} className="card flex flex-wrap items-center justify-between gap-3 p-4">
          <div className="min-w-0">
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase text-slate-600">{s.entity_type}</span>
            <p className="mt-1 text-sm font-bold text-slate-900">{s.label}</p>
            {s.meta?.university && <p className="text-xs text-slate-600">{s.meta.university}</p>}
            {s.meta?.tuition && (
              <p className="text-xs font-semibold text-brand-800">{money(s.meta.tuition, s.meta.currency ?? "USD")}/yr</p>
            )}
          </div>
          <div className="flex gap-2">
            <Link href={href(s)} className="rounded-xl bg-white px-4 py-2 text-sm font-bold text-brand-800 ring-1 ring-slate-300 hover:bg-slate-50">
              Open
            </Link>
            <button type="button" onClick={() => remove(s.id)}
              className="rounded-xl px-3 py-2 text-sm font-bold text-rose-600 hover:bg-rose-50">Remove</button>
          </div>
        </div>
      ))}
      <Link href="/compare" className="block text-center text-sm font-bold text-brand-800 underline">
        Compare your shortlist side by side
      </Link>
    </div>
  );
}

/* ----------------------------------------------------------------- updates */
function Updates({ data, token, reload }: { data: Dict; token: string; reload: () => void }) {
  const markRead = async (id: number) => {
    await portalFetch(`/portal/${token}/notifications/${id}/read`, { method: "POST" });
    reload();
  };

  if (!data.notifications?.length) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-bold text-slate-900">No updates yet</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
          Application status changes, document reviews and deadline reminders will show up here.
        </p>
      </div>
    );
  }

  return (
    <ul className="space-y-3">
      {data.notifications.map((n: Dict) => (
        <li key={n.id} className={`card p-4 ${n.read ? "opacity-70" : "border-l-4 border-l-brand-700"}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-bold text-slate-900">{n.title}</p>
              <p className="mt-0.5 text-sm text-slate-600">{n.body}</p>
              <p className="mt-1 text-[11px] text-slate-400">{String(n.at).slice(0, 16).replace("T", " ")}</p>
            </div>
            {!n.read && (
              <button type="button" onClick={() => markRead(n.id)}
                className="rounded-lg bg-slate-100 px-3 py-1.5 text-[11px] font-bold text-slate-700 hover:bg-slate-200">
                Mark read
              </button>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
