"use client";

import { useCallback, useEffect, useState } from "react";
import { adminFetch, getToken } from "@/lib/admin";
import { ContactFormValues, validateContactForm } from "@/lib/contact";

type Dict = Record<string, any>;

const EMPTY: ContactFormValues = {
  primaryPhone: "", whatsappNumber: "", email: "", facebookUrl: "",
  extraPhones: "", extraEmails: "",
};

/**
 * The four public contact channels, edited as one unit.
 *
 * These are the values printed in the header, the footer, the contact page, the mobile
 * navigation and every WhatsApp deep link, so the form is deliberately narrow: it writes only
 * `phones[0]`, `emails[0]`, `whatsapp_number` and `facebook_page_url` through the validated
 * `/admin/settings/contact` endpoint, and refuses to save until the API accepts them.
 */
export default function ContactSettingsCard() {
  const [values, setValues] = useState<ContactFormValues>(EMPTY);
  const [saved, setSaved] = useState<ContactFormValues>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await adminFetch<Dict>("/settings/contact");
      const next = fromPayload(data);
      setValues(next);
      setSaved(next);
      setFailed("");
    } catch (e: any) {
      setFailed(e?.message ?? "Could not load the contact details.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const set = (key: keyof ContactFormValues, value: string) =>
    setValues((v) => ({ ...v, [key]: value }));

  /** Ask the site to drop its cached copy of the settings right away. */
  const publish = async () => {
    const token = getToken();
    try {
      const res = await fetch("/api/revalidate", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) return "The public site is showing the new details now.";
      if (res.status === 503) return "Saved. The public site will refresh within a few minutes.";
      return "Saved, but the public site could not be refreshed automatically.";
    } catch {
      return "Saved. The public site will refresh within a few minutes.";
    }
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(""); setFailed("");
    const problems = validateContactForm(values);
    setErrors(problems as Record<string, string>);
    if (Object.keys(problems).length) return;

    setBusy(true);
    try {
      const updated = await adminFetch<Dict>("/settings/contact", {
        method: "PUT",
        body: JSON.stringify({
          primary_phone: values.primaryPhone,
          whatsapp_number: values.whatsappNumber,
          email: values.email,
          facebook_url: values.facebookUrl,
          // Sent as the whole list. Leaving them out would keep whatever was stored before,
          // including a number the office has stopped answering.
          extra_phones: splitLines(values.extraPhones),
          extra_emails: splitLines(values.extraEmails),
        }),
      });
      // Show what the server actually stored (E.164, lower-cased email, parsed URL), not the
      // text that was typed - the difference is the normalisation the visitor will see.
      const next = fromPayload(updated);
      setValues(next);
      setSaved(next);
      setErrors({});
      setMessage(await publish());
    } catch (err: any) {
      setFailed(err?.message ?? "Could not save the contact details.");
    } finally {
      setBusy(false);
    }
  };

  const dirty = JSON.stringify(values) !== JSON.stringify(saved);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Public contact details</h2>
          <p className="mt-1 max-w-xl text-xs text-slate-500">
            Shown in the site header, footer, mobile navigation and the contact page, and used
            for the phone, WhatsApp, email and Facebook links. Saving publishes the change to the
            live site without a redeploy.
          </p>
        </div>
        {dirty && <span className="rounded-full bg-amber-100 px-3 py-1 text-[11px] font-bold text-amber-900">Unsaved changes</span>}
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-slate-500">Loading contact details…</p>
      ) : (
        <form onSubmit={save} className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field
            label="Primary phone number"
            hint="Printed in the header and footer, and used for the call button. 01711 223344 or +8801711223344."
            value={values.primaryPhone}
            error={errors.primaryPhone}
            onChange={(v) => set("primaryPhone", v)}
            placeholder="+8801711223344"
            inputMode="tel"
          />
          <Field
            label="WhatsApp number"
            hint="Where the WhatsApp button and wa.me links open a chat."
            value={values.whatsappNumber}
            error={errors.whatsappNumber}
            onChange={(v) => set("whatsappNumber", v)}
            placeholder="+8801711223344"
            inputMode="tel"
          />
          <Field
            label="Public contact email"
            hint="Used for the email link and in the address footer."
            value={values.email}
            error={errors.email}
            onChange={(v) => set("email", v)}
            placeholder="info@example.com"
            inputMode="email"
          />
          <Field
            label="Facebook page URL"
            hint="Full https://facebook.com link. Powers the page link, Messenger button and page panel."
            value={values.facebookUrl}
            error={errors.facebookUrl}
            onChange={(v) => set("facebookUrl", v)}
            placeholder="https://www.facebook.com/yourpage"
            inputMode="url"
          />

          <Field
            label="Additional phone numbers (one per line)"
            hint="Optional. Printed in the footer after the primary number. Clear a line to retire a number that is no longer answered."
            value={values.extraPhones}
            error={errors.extraPhones}
            onChange={(v) => set("extraPhones", v)}
            placeholder="+8801911223344"
            multiline
          />
          <Field
            label="Additional email addresses (one per line)"
            hint="Optional. Also printed in the footer."
            value={values.extraEmails}
            error={errors.extraEmails}
            onChange={(v) => set("extraEmails", v)}
            placeholder="admissions@example.com"
            multiline
          />

          <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
            <button type="submit" disabled={busy}
              className="rounded-xl bg-brand-800 px-6 py-2.5 text-sm font-bold text-white disabled:opacity-60">
              {busy ? "Saving…" : "Save contact details"}
            </button>
            {message && <span className="text-xs font-semibold text-emerald-700">{message}</span>}
          </div>
        </form>
      )}

      {failed && (
        <p className="mt-3 rounded-xl bg-rose-50 p-3 text-sm text-rose-800 ring-1 ring-rose-200">{failed}</p>
      )}
    </section>
  );
}

/** Settings payload -> form values, with the extras joined back into one editable list. */
function fromPayload(data: Dict): ContactFormValues {
  return {
    primaryPhone: data.primary_phone ?? "",
    whatsappNumber: data.whatsapp_number ?? "",
    email: data.email ?? "",
    facebookUrl: data.facebook_url ?? "",
    extraPhones: (data.extra_phones ?? []).join("\n"),
    extraEmails: (data.extra_emails ?? []).join("\n"),
  };
}

function splitLines(value: string): string[] {
  return value.split("\n").map((s) => s.trim()).filter(Boolean);
}

function Field({ label, hint, value, error, onChange, placeholder, inputMode, multiline }: {
  label: string; hint?: string; value: string; error?: string;
  onChange: (value: string) => void; placeholder?: string;
  inputMode?: "tel" | "email" | "url"; multiline?: boolean;
}) {
  const shared = `w-full rounded-xl border px-3 py-2 text-sm outline-none focus:border-brand-600 ${
    error ? "border-rose-400" : "border-slate-300"}`;
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      {multiline ? (
        <textarea value={value} onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder} rows={2} aria-invalid={!!error} className={`${shared} min-h-16`} />
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          inputMode={inputMode}
          aria-invalid={!!error}
          className={shared}
        />
      )}
      {error ? (
        <span className="mt-1 block text-[11px] font-semibold text-rose-700">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>
      ) : null}
    </label>
  );
}
