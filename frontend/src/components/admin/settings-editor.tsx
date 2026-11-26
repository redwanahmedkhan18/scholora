"use client";

import { useEffect, useState } from "react";
import { adminFetch, getToken } from "@/lib/admin";
import ContactSettingsCard from "@/components/admin/contact-settings";

type Dict = Record<string, any>;

const GROUPS: { title: string; hint?: string; fields: string[] }[] = [
  { title: "Brand", fields: ["brand_name", "brand_short", "tagline", "about_short", "logo_url", "favicon_url", "primary_color", "accent_color"] },
  { title: "Office & contact", fields: ["office_address", "office_hours", "phones", "emails", "map_embed_url"] },
  { title: "WhatsApp", hint: "Used by the floating button, the bottom navigation and every “ask on WhatsApp” link.", fields: ["whatsapp_enabled", "whatsapp_number", "whatsapp_default_message"] },
  { title: "Facebook & social", hint: "Linking the Facebook page here enables the page panel, the Messenger button and the footer links.", fields: ["facebook_page_url", "facebook_page_id", "facebook_page_plugin_enabled", "messenger_url", "messenger_enabled", "instagram_url", "linkedin_url", "youtube_url", "tiktok_url", "x_url"] },
  { title: "Google & Meta tracking", hint: "Leave a field blank (or as its XXXX placeholder) and the corresponding script will not load at all.", fields: ["ga4_measurement_id", "gtm_container_id", "google_ads_conversion_id", "google_ads_conversion_label", "google_site_verification", "meta_pixel_id", "meta_domain_verification", "tiktok_pixel_id", "bing_verification", "analytics_consent_required"] },
  { title: "SEO", fields: ["default_seo_title", "default_seo_description", "default_og_image", "site_url", "organization_type", "founding_year"] },
  { title: "Homepage rotation", hint: "hero_rotation_seconds is the default slide duration; any slide can override it with its own duration_seconds.", fields: ["hero_rotation_enabled", "hero_rotation_seconds", "hero_time_scheduling_enabled", "story_rotation_seconds"] },
];

export default function SettingsEditor() {
  const [form, setForm] = useState<Dict | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    adminFetch<Dict>("/settings").then(setForm).catch((e) => setErr(e?.message ?? "Could not load settings."));
  }, []);

  if (err) return <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800 ring-1 ring-rose-200">{err}</p>;
  if (!form) return <p className="text-sm text-slate-500">Loading settings…</p>;

  const set = (k: string, v: any) => setForm((f) => ({ ...(f ?? {}), [k]: v }));

  /** Flush the cached pages so a saved value is live immediately rather than in five minutes. */
  const publish = async () => {
    const token = getToken();
    try {
      const res = await fetch("/api/revalidate", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      return res.ok
        ? "Saved and published — the public site is showing the new values now."
        : "Saved. The public site will refresh within a few minutes.";
    } catch {
      return "Saved. The public site will refresh within a few minutes.";
    }
  };

  const save = async () => {
    setBusy(true); setMsg(""); setErr("");
    try {
      const payload: Dict = { ...form };
      ["id", "created_at", "updated_at"].forEach((k) => delete payload[k]);
      await adminFetch<Dict>("/settings", { method: "PUT", body: JSON.stringify(payload) });
      setForm(await adminFetch<Dict>("/settings"));
      setMsg(await publish());
    } catch (e: any) {
      setErr(e?.message ?? "Save failed.");
    } finally {
      setBusy(false);
    }
  };

  const known = new Set(GROUPS.flatMap((g) => g.fields));
  const rest = Object.keys(form).filter((k) => !known.has(k) && !["id", "created_at", "updated_at"].includes(k));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Site settings</h1>
          <p className="text-sm text-slate-500">One record drives branding, contact channels, tracking IDs, SEO defaults and the hero rotation.</p>
        </div>
        <button type="button" onClick={save} disabled={busy}
          className="rounded-xl bg-brand-800 px-6 py-2.5 text-sm font-bold text-white disabled:opacity-60">
          {busy ? "Saving…" : "Save settings"}
        </button>
      </div>

      <ContactSettingsCard />

      {msg && <p className="rounded-xl bg-emerald-50 p-3 text-sm text-emerald-800 ring-1 ring-emerald-200">{msg}</p>}

      {GROUPS.map((g) => (
        <section key={g.title} className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">{g.title}</h2>
          {g.hint && <p className="mt-1 text-xs text-slate-500">{g.hint}</p>}
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {g.fields.filter((k) => k in form).map((k) => (
              <SettingField key={k} name={k} value={form[k]} onChange={(v) => set(k, v)} />
            ))}
          </div>
        </section>
      ))}

      {rest.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Other fields</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {rest.map((k) => <SettingField key={k} name={k} value={form[k]} onChange={(v) => set(k, v)} />)}
          </div>
        </section>
      )}

      <button type="button" onClick={save} disabled={busy}
        className="rounded-xl bg-brand-800 px-6 py-2.5 text-sm font-bold text-white disabled:opacity-60">
        {busy ? "Saving…" : "Save settings"}
      </button>
    </div>
  );
}

/**
 * A list-valued setting ("one per line").
 *
 * `defaultValue` plus an `onBlur` commit looked fine until the server normalised a value: the
 * textarea then kept showing the old text, so the form and the database silently disagreed
 * about what had been saved. The draft is local and re-seeded whenever the saved value changes.
 */
function ArrayField({ name, label, value, onChange }: {
  name: string; label: string; value: any[]; onChange: (v: any) => void;
}) {
  const saved = (value ?? []).join("\n");
  const [draft, setDraft] = useState(saved);
  useEffect(() => setDraft(saved), [saved]);

  const commit = () => {
    const items = draft.split("\n").map((s) => s.trim()).filter(Boolean);
    if (items.join("\n") !== saved) onChange(items);
  };

  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label} (one per line)</span>
      <textarea value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        aria-label={name}
        className="min-h-20 w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-600" />
    </label>
  );
}

function SettingField({ name, value, onChange }: { name: string; value: any; onChange: (v: any) => void }) {
  const label = name.replace(/_/g, " ");
  const base = "w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-600";

  if (typeof value === "boolean") {
    return (
      <label className="flex items-center gap-2.5 rounded-xl border border-slate-200 px-3 py-2.5 text-sm text-slate-700">
        <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 rounded border-slate-300" />
        <span className="font-medium capitalize">{label}</span>
      </label>
    );
  }
  if (Array.isArray(value)) {
    return <ArrayField name={name} label={label} value={value} onChange={onChange} />;
  }
  const isLong = typeof value === "string" && (value.length > 90 || /about|description|address|message|embed/.test(name));
  const isColor = /color/.test(name);

  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {isLong ? (
        <textarea value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={`${base} min-h-20`} />
      ) : isColor ? (
        <span className="flex items-center gap-2">
          <input type="color" value={value || "#000000"} onChange={(e) => onChange(e.target.value)} className="h-10 w-12 rounded-lg border border-slate-300" />
          <input value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={base} />
        </span>
      ) : (
        <input type={typeof value === "number" ? "number" : "text"} value={value ?? ""}
          onChange={(e) => onChange(typeof value === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)}
          className={base} />
      )}
    </label>
  );
}
