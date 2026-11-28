"use client";

/**
 * Public conversion forms, on React Hook Form + Zod.
 *
 * Previously each of these kept its state in a `useState` object and ran two or three inline
 * `.trim()` checks on submit. That shipped a validator that disagreed with the API: a bad phone
 * number or a 3000-character message only failed server-side, and came back as one red box with
 * no indication of which field was wrong. Now the Zod schema in `lib/schemas.ts` is the single
 * definition, RHF binds it to the fields, and errors land under the input that caused them.
 */

import { useState } from "react";
import { useForm, type Resolver } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { storedUtm, track, whatsappHref } from "@/lib/track";
import {
  appointmentSchema,
  compact,
  eventRegistrationSchema,
  leadSchema,
  type AppointmentValues,
  type EventRegistrationValues,
  type LeadValues,
} from "@/lib/schemas";
import { Button, Checkbox, Input, Label, Textarea } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

type Dict = Record<string, any>;

async function post(path: string, body: Dict) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail ?? `Request failed (${res.status})`);
  return data;
}

/* ------------------------------------------------------------- field shell */

function Field({
  label,
  children,
  hint,
  error,
  className,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
  error?: string;
  className?: string;
}) {
  return (
    <div className={cn("block", className)}>
      <Label className="mb-1">{label}</Label>
      {children}
      {error ? (
        <p role="alert" className="mt-1 text-[11px] font-semibold text-rose-700">
          {error}
        </p>
      ) : (
        hint && <p className="mt-1 text-[11px] text-slate-500">{hint}</p>
      )}
    </div>
  );
}

const selectClass =
  "flex h-10 w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-600/15";

function Done({ title, body, reference }: { title: string; body: string; reference?: string }) {
  return (
    <div role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
      <p className="text-base font-bold text-emerald-900">{title}</p>
      <p className="mt-1 text-sm leading-relaxed text-emerald-800">{body}</p>
      {reference && (
        <p className="mt-2 text-xs font-semibold text-emerald-900">Your reference: {reference}</p>
      )}
    </div>
  );
}

function FormError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-800 ring-1 ring-rose-200">
      {message}
    </p>
  );
}

/* ----------------------------------------------------------------- lead */


/**
 * A controlled-vocabulary option as served by /vocabularies and /settings.
 * Nothing in this file declares its own list any more: degree levels come from the backend
 * vocabulary (app/models/base.py), and appointment services / modes / time slots come from
 * admin-editable site settings. Adding a service or a Saturday slot is a CMS edit.
 */
type Option = { value: string; label: string };

/** Normalise either shape the API can hand us: {value,label} objects or bare strings. */
function asOptions(raw: unknown): Option[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((o) =>
    typeof o === "string" ? { value: o, label: o } : { value: String(o?.value ?? ""), label: String(o?.label ?? o?.value ?? "") },
  ).filter((o) => o.value);
}

function Options({ items }: { items: Option[] }) {
  return (
    <>
      {items.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </>
  );
}

/**
 * The explicit contact-consent control.
 *
 * Deliberately uncontrolled by default and never pre-ticked: submitting a form, typing a phone
 * number or accepting unrelated terms is not permission to be contacted, so the only way this
 * becomes `true` is the visitor actively ticking it. The message is the product's existing
 * wording, shared with the Zod schema so the client and the API agree.
 */
function ConsentCheckbox({
  id,
  label,
  checked,
  onChange,
  error,
}: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  error?: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-start gap-2.5">
        <Checkbox
          id={id}
          checked={checked}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          onCheckedChange={(v) => onChange(v === true)}
          className="mt-0.5"
        />
        <label htmlFor={id} className="text-sm leading-snug text-slate-700">
          {label}
        </label>
      </div>
      {error && (
        <p id={`${id}-error`} role="alert" className="text-[11px] font-semibold text-rose-700">
          {error}
        </p>
      )}
    </div>
  );
}

export function LeadForm({
  countries,
  settings,
  degreeLevels = [],
  compact: isCompact = false,
  source = "WEBSITE",
  title,
}: {
  countries: Dict[];
  settings: Dict;
  degreeLevels?: Option[];
  compact?: boolean;
  source?: string;
  title?: string;
}) {
  const [done, setDone] = useState<{ reference?: string } | null>(null);
  const [submitError, setSubmitError] = useState("");

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<LeadValues>({
    resolver: zodResolver(leadSchema) as Resolver<LeadValues>,
    mode: "onBlur",
    defaultValues: {
      full_name: "",
      phone: "",
      email: "",
      interest_country: "",
      interest_degree: "MASTER",
      interest_subject: "",
      intake: "",
      message: "",
      consent_contact: false,
    },
  });

  const consent = watch("consent_contact");

  const onSubmit = handleSubmit(async (values) => {
    setSubmitError("");
    try {
      const data = await post("/api/v1/leads", {
        ...compact(values),
        source,
        utm: storedUtm(),
        page_path: window.location.pathname,
      });
      setDone({ reference: data.reference });
      track("lead_submit", { source, country: values.interest_country }, "Lead");
    } catch (e: any) {
      setSubmitError(
        typeof e?.message === "string"
          ? e.message
          : "Something went wrong. Please try WhatsApp instead.",
      );
    }
  });

  if (done) {
    return (
      <Done
        title="Thank you — we have your enquiry"
        body="A counselor from the Uttara office will contact you on WhatsApp or by phone within one working day. Nothing is charged for the first conversation."
        reference={done.reference}
      />
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {title && <h2 className="text-lg font-bold text-slate-900">{title}</h2>}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name *" error={errors.full_name?.message}>
          <Input {...register("full_name")} aria-invalid={!!errors.full_name} autoComplete="name" />
        </Field>
        <Field label="Mobile (WhatsApp) *" error={errors.phone?.message}>
          <Input
            {...register("phone")}
            aria-invalid={!!errors.phone}
            inputMode="tel"
            autoComplete="tel"
            placeholder="01712345678"
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Email" error={errors.email?.message}>
          <Input
            type="email"
            {...register("email")}
            aria-invalid={!!errors.email}
            autoComplete="email"
          />
        </Field>
        <Field label="Destination">
          <select className={selectClass} {...register("interest_country")}>
            <option value="">Not decided yet</option>
            {countries
              .filter((c) => c.status !== "COMING_SOON")
              .map((c) => (
                <option key={c.id} value={c.name}>
                  {c.flag_emoji} {c.name}
                </option>
              ))}
          </select>
        </Field>
      </div>

      {!isCompact && (
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Degree level">
            <select className={selectClass} {...register("interest_degree")}>
              <Options items={asOptions(degreeLevels)} />
            </select>
          </Field>
          <Field label="Subject" error={errors.interest_subject?.message}>
            <Input {...register("interest_subject")} placeholder="Computer science" />
          </Field>
          <Field label="Target intake">
            <Input {...register("intake")} placeholder="Fall 2027" />
          </Field>
        </div>
      )}

      <Field label="Your question" error={errors.message?.message}>
        <Textarea
          {...register("message")}
          className="min-h-24"
          placeholder="Tell us your CGPA, English score and budget if you already know them."
        />
      </Field>

      <ConsentCheckbox
        id="consent_contact"
        label="I agree to be contacted about my enquiry on WhatsApp, phone or email."
        checked={!!consent}
        onChange={(v) => setValue("consent_contact", v as true, { shouldValidate: true })}
        error={errors.consent_contact?.message}
      />

      <FormError message={submitError} />

      <div className="flex flex-wrap gap-3">
        <Button type="submit" size="lg" disabled={isSubmitting}>
          {isSubmitting ? "Sending…" : "Send my enquiry"}
        </Button>
        {settings.whatsapp_number && (
          <Button asChild variant="whatsapp" size="lg">
            <a
              href={whatsappHref(settings.whatsapp_number, settings.whatsapp_default_message)}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => track("whatsapp_click", { placement: "lead_form" }, "Contact")}
            >
              Ask on WhatsApp instead
            </a>
          </Button>
        )}
      </div>
    </form>
  );
}

/* ---------------------------------------------------------- appointment */

export function AppointmentForm({ settings }: { settings: Dict }) {
  // Everything the form offers comes from admin-editable settings, including how far ahead
  // the earliest bookable date is. Defaults fall back to the first configured option rather
  // than a hard-coded code, so the form stays valid when the office changes its service list.
  const booking = (settings.appointments ?? {}) as Dict;
  const serviceTypes = asOptions(booking.service_types);
  const modes = asOptions(booking.modes);
  const slots = asOptions(booking.slots);
  const leadDays = Number(booking.lead_days ?? 1);

  const min = new Date(Date.now() + Math.max(0, leadDays) * 86400000).toISOString().slice(0, 10);
  const [done, setDone] = useState<{ reference?: string; mode: string } | null>(null);
  const [submitError, setSubmitError] = useState("");

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<AppointmentValues>({
    resolver: zodResolver(appointmentSchema) as Resolver<AppointmentValues>,
    mode: "onBlur",
    defaultValues: {
      full_name: "",
      phone: "",
      email: "",
      service_type: serviceTypes[0]?.value ?? "",
      scheduled_date: min,
      scheduled_time: slots[0]?.value ?? "",
      mode: modes[0]?.value ?? "",
      notes: "",
      consent_contact: false,
    },
  });

  const consent = watch("consent_contact");

  const onSubmit = handleSubmit(async (values) => {
    setSubmitError("");
    try {
      const data = await post("/api/v1/appointments", { ...compact(values), utm: storedUtm() });
      setDone({ reference: data.reference, mode: values.mode });
      track("appointment_booked", { service: values.service_type, mode: values.mode }, "Schedule");
    } catch (e: any) {
      setSubmitError(
        typeof e?.message === "string" ? e.message : "Booking failed. Please call or WhatsApp us.",
      );
    }
  });

  if (done) {
    return (
      <Done
        title="Your session is requested"
        body={`We will confirm your slot on WhatsApp. ${
          done.mode === "OFFLINE"
            ? (settings.office_address ?? "")
            : "You will receive an online meeting link."
        }`}
        reference={done.reference}
      />
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name *" error={errors.full_name?.message}>
          <Input {...register("full_name")} aria-invalid={!!errors.full_name} autoComplete="name" />
        </Field>
        <Field label="Mobile (WhatsApp) *" error={errors.phone?.message}>
          <Input
            {...register("phone")}
            aria-invalid={!!errors.phone}
            inputMode="tel"
            autoComplete="tel"
            placeholder="01712345678"
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Email" error={errors.email?.message}>
          <Input type="email" {...register("email")} aria-invalid={!!errors.email} />
        </Field>
        <Field label="What do you need?" error={errors.service_type?.message}>
          <select className={selectClass} {...register("service_type")}>
            <Options items={serviceTypes} />
          </select>
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Date *" error={errors.scheduled_date?.message}>
          <Input
            type="date"
            min={min}
            {...register("scheduled_date")}
            aria-invalid={!!errors.scheduled_date}
          />
        </Field>
        <Field label="Time *" error={errors.scheduled_time?.message}>
          <select className={selectClass} {...register("scheduled_time")}>
            <Options items={slots} />
          </select>
        </Field>
        <Field label="Mode">
          <select className={selectClass} {...register("mode")}>
            <Options items={modes} />
          </select>
        </Field>
      </div>

      <Field
        label="Anything we should prepare?"
        hint={settings.office_hours ? `Office hours: ${settings.office_hours}` : undefined}
        error={errors.notes?.message}
      >
        <Textarea {...register("notes")} className="min-h-20" />
      </Field>

      <ConsentCheckbox
        id="appointment_consent_contact"
        label="I agree to be contacted about this booking on WhatsApp, phone or email."
        checked={!!consent}
        onChange={(v) => setValue("consent_contact", v as true, { shouldValidate: true })}
        error={errors.consent_contact?.message}
      />

      <FormError message={submitError} />

      <Button type="submit" size="lg" disabled={isSubmitting}>
        {isSubmitting ? "Booking…" : "Request this slot"}
      </Button>
    </form>
  );
}

/* ------------------------------------------------------ event registration */

export function EventRegister({ event, countries, degreeLevels = [] }:
  { event: Dict; countries: Dict[]; degreeLevels?: Option[] }) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<EventRegistrationValues>({
    resolver: zodResolver(eventRegistrationSchema) as Resolver<EventRegistrationValues>,
    mode: "onBlur",
    defaultValues: {
      full_name: "",
      phone: "",
      email: "",
      preferred_country: "",
      degree_level: "MASTER",
      consent_contact: false,
    },
  });

  const consent = watch("consent_contact");

  const onSubmit = handleSubmit(async (values) => {
    setSubmitError("");
    try {
      await post("/api/v1/events/register", { event_id: event.id, ...compact(values) });
      setDone(true);
      track("event_register", { event: event.slug }, "CompleteRegistration");
    } catch (e: any) {
      setSubmitError(
        typeof e?.message === "string" ? e.message : "Registration failed. Please WhatsApp us.",
      );
    }
  });

  if (done) {
    return (
      <Done
        title="You are registered"
        body={`We will send a reminder before ${event.title}. Bring your academic documents and passport copy.`}
      />
    );
  }

  if (!open) {
    return (
      <Button
        type="button"
        onClick={() => {
          setOpen(true);
          track("event_register_open", { event: event.slug });
        }}
      >
        Register free
      </Button>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      className="mt-4 space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Full name *" error={errors.full_name?.message}>
          <Input {...register("full_name")} aria-invalid={!!errors.full_name} />
        </Field>
        <Field label="Mobile *" error={errors.phone?.message}>
          <Input {...register("phone")} aria-invalid={!!errors.phone} inputMode="tel" />
        </Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Email" error={errors.email?.message}>
          <Input type="email" {...register("email")} aria-invalid={!!errors.email} />
        </Field>
        <Field label="Destination">
          <select className={selectClass} {...register("preferred_country")}>
            <option value="">Not decided</option>
            {countries.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Level">
          <select className={selectClass} {...register("degree_level")}>
            <Options items={asOptions(degreeLevels)} />
          </select>
        </Field>
      </div>

      <ConsentCheckbox
        id="event_consent_contact"
        label="I agree to be contacted about this event on WhatsApp, phone or email."
        checked={!!consent}
        onChange={(v) => setValue("consent_contact", v as true, { shouldValidate: true })}
        error={errors.consent_contact?.message}
      />

      <FormError message={submitError} />

      <div className="flex gap-2">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Registering…" : "Confirm my seat"}
        </Button>
        <Button type="button" variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
