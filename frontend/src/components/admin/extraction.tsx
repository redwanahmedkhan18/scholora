"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AuthError, adminFetch } from "@/lib/admin";

type Dict = Record<string, any>;

type Finding = {
  intent: string;
  quote: string;
  source_url: string;
  page_title?: string;
  score?: number;
  method?: string;
  note?: string;
};

type Result = {
  resolution: { status: string; message: string; website: string;
                university_name?: string | null; university_slug?: string | null;
                candidates?: { name: string; slug: string; website: string }[] };
  intents: string[];
  pages: { url: string; status: string; title: string; chars: number }[];
  findings: Finding[];
  dropped_ungrounded: number;
  retrieved_at: string;
  confidence: string;
  notice: string;
  method?: string;
  llm_note?: string;
  elapsed_ms?: number;
};

const CARD: React.CSSProperties = {
  border: "1px solid #e2e8f0", borderRadius: 12, background: "#fff",
  padding: "1rem 1.1rem", marginBottom: "0.9rem",
};
const INPUT: React.CSSProperties = {
  width: "100%", padding: "0.55rem 0.7rem", border: "1px solid #cbd5e1",
  borderRadius: 8, fontSize: "0.92rem", fontFamily: "inherit",
};
const LABEL: React.CSSProperties = {
  display: "block", fontSize: "0.78rem", fontWeight: 600, color: "#475569",
  marginBottom: "0.3rem", textTransform: "uppercase", letterSpacing: "0.03em",
};

const INTENT_TONES: Dict = {
  tuition: ["#dbeafe", "#1e40af"], entry_requirements: ["#dcfce7", "#166534"],
  deadlines: ["#fef3c7", "#92400e"], scholarships: ["#fae8ff", "#86198f"],
  programs: ["#e0e7ff", "#3730a3"], contact: ["#f1f5f9", "#475569"],
  application_process: ["#ffedd5", "#9a3412"],
};

function Tag({ name }: { name: string }) {
  const [bg, fg] = INTENT_TONES[name] ?? ["#f1f5f9", "#475569"];
  return (
    <span style={{ background: bg, color: fg, borderRadius: 999, padding: "0.1rem 0.55rem",
                   fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.02em" }}>
      {name.replace(/_/g, " ")}
    </span>
  );
}

export default function ExtractionWorkbench() {
  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [instruction, setInstruction] = useState("");
  const [website, setWebsite] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const [maxPages, setMaxPages] = useState(8);

  const run = useMutation<Result, Error, void>({
    mutationFn: () =>
      adminFetch<Result>("/extraction/run", {
        method: "POST",
        body: JSON.stringify({ name, country, instruction, website,
                               max_pages: maxPages, use_llm: useLlm }),
      }),
  });

  const result = run.data ?? null;
  const err = run.error
    ? run.error instanceof AuthError ? run.error.message : run.error.message
    : "";

  return (
    <div>
      <h1 style={{ fontSize: "1.45rem", margin: "0 0 0.3rem" }}>Read a university website</h1>
      <p style={{ color: "#475569", margin: "0 0 1.1rem", maxWidth: "62ch", fontSize: "0.92rem" }}>
        Give a university, a country and a question in plain words. The reader visits the
        institution&apos;s own site, follows the pages most likely to answer, and returns
        quotes. Every quote is checked to appear <strong>verbatim</strong> on the page it
        cites — nothing here is paraphrased or inferred.
      </p>

      <div style={CARD}>
        <div style={{ display: "grid", gap: "0.8rem", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))" }}>
          <div>
            <label style={LABEL} htmlFor="ex-name">University name</label>
            <input id="ex-name" style={INPUT} value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="Trinity College Dublin" />
          </div>
          <div>
            <label style={LABEL} htmlFor="ex-country">Country</label>
            <input id="ex-country" style={INPUT} value={country} onChange={(e) => setCountry(e.target.value)}
                   placeholder="Ireland" />
          </div>
        </div>
        <div style={{ marginTop: "0.8rem" }}>
          <label style={LABEL} htmlFor="ex-q">What do you want to know?</label>
          <input id="ex-q" style={INPUT} value={instruction} onChange={(e) => setInstruction(e.target.value)}
                 placeholder="What are the tuition fees and IELTS requirements for international master's students?" />
        </div>
        <div style={{ marginTop: "0.8rem" }}>
          <label style={LABEL} htmlFor="ex-site">Official website (only if not in the catalogue)</label>
          <input id="ex-site" style={INPUT} value={website} onChange={(e) => setWebsite(e.target.value)}
                 placeholder="https://www.example.edu/" />
          <p style={{ fontSize: "0.78rem", color: "#64748b", margin: "0.35rem 0 0" }}>
            For universities already in the catalogue this is filled in automatically from the
            verified record. The reader will never guess a domain from a name.
          </p>
        </div>
        <div style={{ display: "flex", gap: "1.2rem", alignItems: "center", marginTop: "0.9rem", flexWrap: "wrap" }}>
          <label style={{ fontSize: "0.85rem", display: "flex", gap: "0.4rem", alignItems: "center" }}>
            <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
            Use the AI model to pick passages
          </label>
          <label style={{ fontSize: "0.85rem", display: "flex", gap: "0.4rem", alignItems: "center" }}>
            Pages to read
            <input type="number" min={1} max={12} value={maxPages}
                   onChange={(e) => setMaxPages(Number(e.target.value))}
                   style={{ ...INPUT, width: 70, padding: "0.3rem 0.4rem" }} />
          </label>
          <button
            onClick={() => run.mutate()}
            disabled={run.isPending || (!name.trim() && !website.trim())}
            style={{ marginLeft: "auto", background: "#0f3d8a", color: "#fff", border: 0,
                     borderRadius: 8, padding: "0.6rem 1.3rem", fontWeight: 700,
                     cursor: run.isPending ? "wait" : "pointer", fontSize: "0.9rem" }}>
            {run.isPending ? "Reading the site…" : "Read the website"}
          </button>
        </div>
      </div>

      {err && (
        <div style={{ ...CARD, borderColor: "#fecaca", background: "#fef2f2", color: "#991b1b" }}>
          {err}
        </div>
      )}

      {result && (
        <>
          <div style={{ ...CARD,
                        borderColor: result.resolution.status === "RESOLVED" ? "#e2e8f0" : "#fed7aa",
                        background: result.resolution.status === "RESOLVED" ? "#fff" : "#fffbeb" }}>
            <strong style={{ fontSize: "0.92rem" }}>{result.resolution.status.replace(/_/g, " ")}</strong>
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.88rem", color: "#334155" }}>
              {result.resolution.message}
            </p>
            {result.resolution.website && (
              <p style={{ margin: "0.35rem 0 0", fontSize: "0.85rem" }}>
                Read from <a href={result.resolution.website} target="_blank" rel="noreferrer noopener">
                  {result.resolution.website}</a>
                {typeof result.elapsed_ms === "number" && (
                  <span style={{ color: "#64748b" }}> · {(result.elapsed_ms / 1000).toFixed(1)}s</span>
                )}
              </p>
            )}
            {!!result.resolution.candidates?.length && (
              <div style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
                Did you mean:{" "}
                {result.resolution.candidates.map((c) => (
                  <button key={c.slug} onClick={() => setName(c.name)}
                          style={{ background: "none", border: "1px solid #cbd5e1", borderRadius: 6,
                                   padding: "0.1rem 0.45rem", marginRight: "0.35rem", cursor: "pointer" }}>
                    {c.name}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div style={{ ...CARD, background: "#f8fafc" }}>
            <p style={{ margin: 0, fontSize: "0.85rem", color: "#334155" }}>{result.notice}</p>
            <div style={{ display: "flex", gap: "1.3rem", flexWrap: "wrap", marginTop: "0.6rem",
                          fontSize: "0.8rem", color: "#475569" }}>
              <span><strong>{result.findings.length}</strong> findings</span>
              <span><strong>{result.pages.filter((p) => p.status === "OK").length}</strong>/{result.pages.length} pages read</span>
              <span>method <strong>{result.method ?? "pattern"}</strong></span>
              <span title="Quotes the model produced that could not be found on any page we fetched, and were therefore thrown away.">
                <strong>{result.dropped_ungrounded}</strong> ungrounded quotes discarded
              </span>
              <span>confidence <strong>{result.confidence}</strong></span>
            </div>
            {result.llm_note && (
              <p style={{ margin: "0.5rem 0 0", fontSize: "0.8rem", color: "#92400e" }}>
                AI model not used: {result.llm_note}. Pattern matching was used instead.
              </p>
            )}
          </div>

          {result.findings.map((f, i) => (
            <div key={i} style={CARD}>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.45rem" }}>
                <Tag name={f.intent} />
                {f.method === "llm" && (
                  <span style={{ fontSize: "0.7rem", color: "#64748b" }}>selected by AI, quote verified</span>
                )}
              </div>
              <blockquote style={{ margin: 0, paddingLeft: "0.8rem", borderLeft: "3px solid #0f3d8a",
                                   fontSize: "0.93rem", lineHeight: 1.55, color: "#0f172a" }}>
                {f.quote}
              </blockquote>
              <p style={{ margin: "0.5rem 0 0", fontSize: "0.78rem" }}>
                <a href={f.source_url} target="_blank" rel="noreferrer noopener"
                   style={{ color: "#475569", wordBreak: "break-all" }}>{f.source_url}</a>
              </p>
              {f.note && (
                <p style={{ margin: "0.25rem 0 0", fontSize: "0.75rem", color: "#92400e" }}>{f.note}</p>
              )}
            </div>
          ))}

          {!!result.pages.length && (
            <details style={CARD}>
              <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: "0.9rem" }}>
                Pages read ({result.pages.length})
              </summary>
              <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "0.6rem", fontSize: "0.82rem" }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "#64748b" }}>
                    <th style={{ padding: "0.3rem 0" }}>Status</th>
                    <th>Characters</th><th>URL</th>
                  </tr>
                </thead>
                <tbody>
                  {result.pages.map((p) => (
                    <tr key={p.url} style={{ borderTop: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "0.3rem 0.5rem 0.3rem 0" }}>{p.status}</td>
                      <td style={{ padding: "0.3rem 0.5rem 0.3rem 0" }}>{p.chars}</td>
                      <td style={{ wordBreak: "break-all" }}>
                        <a href={p.url} target="_blank" rel="noreferrer noopener">{p.url}</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </>
      )}
    </div>
  );
}
