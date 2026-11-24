"use client";

import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AuthError, adminFetch } from "@/lib/admin";

type Dict = Record<string, any>;

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

const CARD: React.CSSProperties = {
  border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff",
  padding: "1rem 1.1rem", marginBottom: "0.9rem",
};

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  const tones: Dict = {
    READY: ["#dcfce7", "#166534"], MISSING: ["#fef3c7", "#92400e"],
    OPTIONAL: ["#e0e7ff", "#3730a3"], OK: ["#dcfce7", "#166534"],
    BROKEN: ["#fee2e2", "#991b1b"], BLOCKED: ["#fef3c7", "#92400e"],
    UNREACHABLE: ["#e2e8f0", "#475569"], UNCHECKED: ["#f1f5f9", "#64748b"],
  };
  const [bg, fg] = tones[tone] ?? ["#f1f5f9", "#475569"];
  return (
    <span style={{
      background: bg, color: fg, borderRadius: 999, padding: "2px 10px",
      fontSize: 11, fontWeight: 700, letterSpacing: 0.3,
    }}>{children}</span>
  );
}

/* ===================================================== go-live readiness ==== */
function GoLive() {
  const { data, error, loading, reload } = useResource("/readiness");
  const [testing, setTesting] = useState("");
  const [results, setResults] = useState<Dict>({});

  async function runTest(target: string) {
    setTesting(target);
    try {
      const res = await adminFetch<Dict>(`/config/test/${target}`, { method: "POST" });
      setResults((r) => ({ ...r, [target]: res }));
    } catch (e: any) {
      setResults((r) => ({ ...r, [target]: { ok: false, detail: e?.message ?? "failed" } }));
    } finally {
      setTesting("");
    }
  }

  if (loading) return <p style={{ color: "#64748b" }}>Loading readiness…</p>;
  if (error) return <p style={{ color: "#b91c1c" }}>{error}</p>;
  if (!data) return null;

  return (
    <section>
      <div style={{ ...CARD, borderLeft: "4px solid #0f3d8a" }}>
        <strong style={{ fontSize: 15 }}>{data.summary}</strong>
        <p style={{ margin: "0.45rem 0 0", color: "#475569", fontSize: 13, lineHeight: 1.6 }}>
          Each item below needs an account only you can open. Nothing here blocks the site:
          every one has a working fallback, listed as <em>Without it</em>. Paste a value in
          Platform settings, then press <strong>Test</strong> to prove it works before you
          rely on it.
        </p>
      </div>

      {data.items?.map((item: Dict) => {
        const result = results[item.test];
        return (
          <article key={item.key} style={CARD}>
            <header style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <Badge tone={item.status}>{item.status}</Badge>
              <strong style={{ fontSize: 15 }}>{item.label}</strong>
              <span style={{ marginLeft: "auto", fontSize: 12, color: "#64748b" }}>
                {item.effort}
              </span>
            </header>

            <p style={{ margin: "0.6rem 0 0.4rem", fontSize: 13, color: "#334155", lineHeight: 1.6 }}>
              {item.why}
            </p>

            <dl style={{ margin: "0.5rem 0 0", fontSize: 12.5, color: "#475569", lineHeight: 1.65 }}>
              <div><strong>Where to get it: </strong>{item.where}</div>
              <div style={{ marginTop: 4 }}><strong>Without it: </strong>{item.degrades_to}</div>
              {item.blocks?.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  <strong>Currently unavailable: </strong>{item.blocks.join(" · ")}
                </div>
              )}
            </dl>

            <div style={{ marginTop: "0.7rem", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              {item.settings?.map((s: Dict) => (
                <code key={s.key} style={{
                  fontSize: 11, padding: "2px 8px", borderRadius: 6,
                  background: s.present ? "#dcfce7" : "#f1f5f9",
                  color: s.present ? "#166534" : "#64748b",
                }}>
                  {s.present ? "✓ " : "○ "}{s.key}
                </code>
              ))}
              <button
                onClick={() => runTest(item.test)}
                disabled={testing === item.test}
                style={{
                  marginLeft: "auto", fontSize: 12, padding: "5px 14px", borderRadius: 8,
                  border: "1px solid #0f3d8a", background: "#fff", color: "#0f3d8a",
                  cursor: "pointer", fontWeight: 600,
                }}
              >
                {testing === item.test ? "Testing…" : "Test connection"}
              </button>
            </div>

            {result && (
              <p style={{
                marginTop: "0.6rem", marginBottom: 0, fontSize: 12.5, padding: "8px 10px",
                borderRadius: 8, background: result.ok ? "#f0fdf4" : "#fef2f2",
                color: result.ok ? "#166534" : "#991b1b",
              }}>
                {result.ok ? "✓ " : "✗ "}{result.detail ?? result.message ?? JSON.stringify(result)}
              </p>
            )}
          </article>
        );
      })}

      <button onClick={reload} style={{
        fontSize: 12, padding: "6px 14px", borderRadius: 8, border: "1px solid #cbd5e1",
        background: "#fff", cursor: "pointer",
      }}>Refresh</button>
    </section>
  );
}

/* ================================================ source verification ======= */
function Sources() {
  const { data, error, loading, reload } = useResource("/verification/sources?limit=200");
  const [busy, setBusy] = useState("");
  const [verifier, setVerifier] = useState("");
  const [note, setNote] = useState("");

  async function check() {
    setBusy("check");
    try {
      await adminFetch("/verification/sources/check?limit=200", { method: "POST" });
      await reload();
    } finally { setBusy(""); }
  }

  async function verify(url: string) {
    if (!verifier.trim()) { setNote("Enter your name first — VERIFIED has to be attributable."); return; }
    setBusy(url);
    try {
      const res = await adminFetch<Dict>("/verification/sources/verify", {
        method: "POST",
        body: JSON.stringify({ source_url: url, verifier, note: "" }),
      });
      setNote(`Confirmed ${res.verified} claims from that page as ${res.verified_by}.`);
      await reload();
    } catch (e: any) {
      setNote(e?.message ?? "Could not record that.");
    } finally { setBusy(""); }
  }

  if (loading) return <p style={{ color: "#64748b" }}>Loading sources…</p>;
  if (error) return <p style={{ color: "#b91c1c" }}>{error}</p>;
  if (!data) return null;

  const items: Dict[] = data.items ?? [];
  const pending = items.reduce((n, i) => n + i.pending, 0);

  return (
    <section>
      <div style={{ ...CARD, borderLeft: "4px solid #f59e0b" }}>
        <strong style={{ fontSize: 15 }}>
          {pending.toLocaleString()} unverified claims across {items.length} pages
        </strong>
        <p style={{ margin: "0.45rem 0 0", color: "#475569", fontSize: 13, lineHeight: 1.6 }}>
          The backlog is grouped by the page you would actually open, because one
          university fee page typically supports seventy to a hundred separate figures.
          Open a page, check the figures we hold against it, then confirm the whole page
          in one action. <strong>Reachability is checked automatically; it is not
          verification</strong> — a machine can tell you a page still loads, not that it
          still says what we claim.
        </p>
        <div style={{ marginTop: "0.8rem", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            value={verifier}
            onChange={(e) => setVerifier(e.target.value)}
            placeholder="Your name — recorded against every confirmation"
            style={{
              flex: "1 1 280px", padding: "7px 10px", borderRadius: 8,
              border: "1px solid #cbd5e1", fontSize: 13,
            }}
          />
          <button onClick={check} disabled={busy === "check"} style={{
            fontSize: 12, padding: "7px 14px", borderRadius: 8, border: "1px solid #0f3d8a",
            background: "#fff", color: "#0f3d8a", cursor: "pointer", fontWeight: 600,
          }}>{busy === "check" ? "Checking…" : "Re-check all links"}</button>
        </div>
        {note && <p style={{ margin: "0.6rem 0 0", fontSize: 12.5, color: "#0f3d8a" }}>{note}</p>}
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: "0.8rem" }}>
        {Object.entries(data.states ?? {}).map(([state, count]) => (
          <span key={state} style={{ fontSize: 12, color: "#475569" }}>
            <Badge tone={state}>{state}</Badge> {String(count)}
          </span>
        ))}
      </div>

      {items.map((item) => (
        <article key={item.source_url} style={CARD}>
          <header style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <Badge tone={item.link_state}>{item.link_state}</Badge>
            <a href={item.source_url} target="_blank" rel="noreferrer"
               style={{ fontSize: 13, color: "#0f3d8a", wordBreak: "break-all", fontWeight: 600 }}>
              {item.source_url}
            </a>
          </header>
          <p style={{ margin: "0.5rem 0 0", fontSize: 12.5, color: "#475569" }}>
            <strong>{item.claims}</strong> claims · {item.pending} pending ·{" "}
            {Object.entries(item.entities ?? {}).map(([k, v]) => `${k} ×${v}`).join(", ")}
            {item.note ? ` · ${item.note}` : ""}
          </p>
          {item.pending > 0 && (
            <button onClick={() => verify(item.source_url)} disabled={busy === item.source_url}
              style={{
                marginTop: "0.6rem", fontSize: 12, padding: "6px 14px", borderRadius: 8,
                border: "1px solid #166534", background: "#fff", color: "#166534",
                cursor: "pointer", fontWeight: 600,
              }}>
              {busy === item.source_url ? "Recording…" : `I checked this page — confirm ${item.pending} claims`}
            </button>
          )}
        </article>
      ))}
    </section>
  );
}

/* ============================================================== container === */
export default function ReadinessWorkbench() {
  const [tab, setTab] = useState<"golive" | "sources">("golive");
  const tabs: [string, string][] = [["golive", "Go-live checklist"], ["sources", "Source verification"]];

  return (
    <div>
      <nav style={{ display: "flex", gap: 6, marginBottom: "1rem", flexWrap: "wrap" }}>
        {tabs.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key as any)} style={{
            fontSize: 13, padding: "7px 16px", borderRadius: 999, cursor: "pointer",
            fontWeight: 600,
            border: tab === key ? "1px solid #0f3d8a" : "1px solid #e2e8f0",
            background: tab === key ? "#0f3d8a" : "#fff",
            color: tab === key ? "#fff" : "#475569",
          }}>{label}</button>
        ))}
      </nav>
      {tab === "golive" ? <GoLive /> : <Sources />}
    </div>
  );
}
