"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { adminFetch, getToken } from "@/lib/admin";
import { money } from "@/lib/format";

type Dict = Record<string, any>;

/**
 * Marketing hub: Facebook page linking, Meta Pixel + Conversions API, GA4 / Google Ads /
 * Search Console, UTM builder and campaign performance. All IDs live in site settings, so
 * nothing here is hard-coded into the frontend bundle.
 */
export default function AdminMarketing() {
  const [links, setLinks] = useState<Dict | null>(null);
  const [settings, setSettings] = useState<Dict | null>(null);
  const [campaigns, setCampaigns] = useState<Dict[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [s, c] = await Promise.all([
          adminFetch<Dict>("/settings"),
          adminFetch<Dict>("/campaigns?page_size=50"),
        ]);
        setSettings(s);
        setCampaigns(c.items ?? []);
        const token = getToken();
        const res = await fetch("/api/v1/marketing/links", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
        if (res.ok) setLinks(await res.json());
      } catch (e: any) {
        setErr(e?.message ?? "Could not load marketing data.");
      }
    })();
  }, []);

  if (err) return <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">{err}</p>;
  if (!settings) return <p className="text-sm text-slate-500">Loading marketing hub…</p>;

  const placeholder = (v?: string) => !v || /X{4,}|0{6,}/.test(v);
  const integrations: [string, string | undefined, string, string][] = [
    ["Facebook page", settings.facebook_page_url, "Page link, Messenger button and page panel on the website.", "/admin/settings"],
    ["Meta Pixel", settings.meta_pixel_id, "Client-side pixel for PageView, Lead, Contact and Search events.", "/admin/settings"],
    ["Meta Conversions API", process.env.NEXT_PUBLIC_CAPI ?? undefined, "Server-side events with SHA-256 hashed PII (set META_CAPI_TOKEN in the backend .env).", "/admin/settings"],
    ["GA4", settings.ga4_measurement_id, "Page views plus every first-party event the site already records.", "/admin/settings"],
    ["Google Tag Manager", settings.gtm_container_id, "Optional container if you prefer managing tags in GTM.", "/admin/settings"],
    ["Google Ads", settings.google_ads_conversion_id, "Conversion ID and label for lead and appointment conversions.", "/admin/settings"],
    ["Search Console", settings.google_site_verification, "Verification meta tag rendered into the site head.", "/admin/settings"],
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Marketing &amp; ads</h1>
        <p className="text-sm text-slate-500">
          Connect the Facebook page, pixels and Google properties, build UTM links, and watch which campaign actually
          produced the leads.
        </p>
      </div>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Integrations</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2">Integration</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Value</th><th className="px-3 py-2">What it does</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {integrations.map(([name, value, what, href]) => (
                <tr key={name}>
                  <td className="whitespace-nowrap px-3 py-2.5 font-semibold text-slate-900">{name}</td>
                  <td className="px-3 py-2.5">
                    {placeholder(value)
                      ? <span className="chip bg-amber-50 text-amber-700 ring-1 ring-amber-200">Not connected</span>
                      : <span className="chip bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200">Connected</span>}
                  </td>
                  <td className="break-all px-3 py-2.5 font-mono text-[11px] text-slate-600">{value || "—"}</td>
                  <td className="px-3 py-2.5 text-xs text-slate-600">
                    {what} <Link href={href} className="font-semibold text-brand-800">Edit →</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
          Placeholder values (<code>G-XXXXXXXXXX</code>, <code>000000000000000</code>) are treated as “not connected”
          and no third-party script is loaded. Replace them in Site settings when the real accounts exist.
        </p>
      </section>

      {links && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Channel links</h2>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {Object.entries(links).filter(([, v]) => typeof v === "string").map(([k, v]) => (
              <li key={k} className="rounded-xl border border-slate-200 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{k.replace(/_/g, " ")}</p>
                <a href={String(v)} target="_blank" rel="noopener noreferrer" className="break-all text-xs text-brand-700 underline underline-offset-2">{String(v)}</a>
              </li>
            ))}
          </ul>
          {links.default_landing_links && (
            <>
              <h3 className="mt-5 text-xs font-bold uppercase tracking-wide text-slate-600">Ready-made UTM landing links</h3>
              <ul className="mt-2 space-y-1.5">
                {Object.entries(links.default_landing_links as Dict).map(([k, v]) => (
                  <li key={k} className="flex flex-wrap items-center gap-2">
                    <span className="chip bg-slate-100 text-slate-700">{k.replace(/_/g, " ")}</span>
                    <code className="break-all text-[11px] text-slate-600">{String(v)}</code>
                    <CopyButton value={String(v)} />
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <UtmBuilder siteUrl={settings.site_url} />

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Campaigns</h2>
          <Link href="/admin/campaigns" className="text-[11px] font-semibold text-brand-800">Manage campaigns →</Link>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2">Campaign</th><th className="px-3 py-2">Channel</th><th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Landing</th><th className="px-3 py-2">UTM campaign</th>
                <th className="px-3 py-2">Budget</th><th className="px-3 py-2">Spend</th><th className="px-3 py-2">Clicks</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {campaigns.map((c) => (
                <tr key={c.id}>
                  <td className="px-3 py-2.5 font-semibold text-slate-900">{c.name}</td>
                  <td className="px-3 py-2.5 text-slate-700">{c.channel}</td>
                  <td className="px-3 py-2.5">
                    <span className={`chip ring-1 ${c.status === "RUNNING" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-600 ring-slate-200"}`}>{c.status}</span>
                  </td>
                  <td className="px-3 py-2.5 text-slate-600">{c.landing_path}</td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-slate-600">{c.utm_campaign}</td>
                  <td className="px-3 py-2.5 text-slate-700">{money(c.total_budget, c.currency ?? "BDT")}</td>
                  <td className="px-3 py-2.5 text-slate-700">{money(c.spend_to_date, c.currency ?? "BDT")}</td>
                  <td className="px-3 py-2.5 text-slate-700">{c.clicks ?? 0}</td>
                </tr>
              ))}
              {campaigns.length === 0 && <tr><td colSpan={8} className="px-3 py-6 text-center text-slate-500">No campaigns yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs leading-relaxed text-slate-600">
          A campaign can also drive the homepage: set <code>campaign_id</code> on a slide and raise its priority so the
          hero matches the ad a visitor just clicked.
        </p>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">How attribution works here</h2>
        <ol className="mt-3 space-y-2 text-sm text-slate-700">
          <li><strong>1.</strong> A visitor arrives with UTM parameters; the browser stores them for the session.</li>
          <li><strong>2.</strong> Every lead, appointment and event registration is submitted with those UTM values attached.</li>
          <li><strong>3.</strong> The backend maps <code>utm_campaign</code> to an AdCampaign record, so the pipeline shows which ad produced which student.</li>
          <li><strong>4.</strong> Server-side conversions are posted to the Meta Conversions API with SHA-256 hashed email and phone — raw PII never leaves the server.</li>
          <li><strong>5.</strong> If no CAPI token is configured the conversion is logged locally instead, so nothing silently disappears.</li>
        </ol>
      </section>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button"
      onClick={() => { navigator.clipboard?.writeText(value); setDone(true); setTimeout(() => setDone(false), 1500); }}
      className="rounded-lg border border-slate-300 px-2 py-0.5 text-[10px] font-bold text-slate-600">
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function UtmBuilder({ siteUrl }: { siteUrl?: string }) {
  const [f, setF] = useState({
    base: siteUrl ?? "", path: "/profile-assessment", source: "facebook", medium: "paid_social",
    campaign: "fall_2027_usa", content: "video_01", term: "",
  });
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));
  const sp = new URLSearchParams();
  if (f.source) sp.set("utm_source", f.source);
  if (f.medium) sp.set("utm_medium", f.medium);
  if (f.campaign) sp.set("utm_campaign", f.campaign);
  if (f.content) sp.set("utm_content", f.content);
  if (f.term) sp.set("utm_term", f.term);
  const url = `${(f.base || "").replace(/\/$/, "")}${f.path}?${sp.toString()}`;

  const input = "w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-600";

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">UTM link builder</h2>
      <p className="mt-1 text-xs text-slate-500">Build the exact link to paste into a Facebook or Google ad so the lead can be attributed back to it.</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        {[["base", "Site URL"], ["path", "Landing path"], ["source", "utm_source"],
          ["medium", "utm_medium"], ["campaign", "utm_campaign"], ["content", "utm_content"], ["term", "utm_term"]].map(([k, label]) => (
          <label key={k} className="block">
            <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
            <input className={input} value={(f as any)[k]} onChange={(e) => set(k, e.target.value)} />
          </label>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 p-3">
        <code className="break-all text-xs text-slate-700">{url}</code>
        <CopyButton value={url} />
      </div>
    </section>
  );
}
