/**
 * Zod schemas mirroring the FastAPI request models in
 * `backend/app/api/v1/routers/actions.py`.
 *
 * Why this file exists: before it, each form re-implemented its own checks inline, so the
 * browser and the server disagreed about what "valid" meant - the client would happily send a
 * malformed Bangladeshi mobile number and let a 422 come back as a generic red box. One schema
 * per endpoint gives a single definition of the contract, the inferred TypeScript type for the
 * form state, and messages written for the student rather than for a validator.
 *
 * Keep in step with the Pydantic models. Where the API is permissive (most fields Optional) the
 * schema stays permissive too: validation here is about catching real mistakes early, not about
 * inventing requirements the backend does not have.
 */

import { z } from "zod";

/* ----------------------------------------------------------------- shared */

/** Bangladeshi mobile: 01XXXXXXXXX, optionally +880 / 880 prefixed, spaces and dashes allowed. */
const BD_MOBILE = /^(?:\+?880[\s-]?|0)1[3-9]\d{2}[\s-]?\d{6}$/;

export const phoneSchema = z
  .string()
  .trim()
  .min(1, "Mobile number is required.")
  .refine((v) => BD_MOBILE.test(v) || /^\+\d{7,15}$/.test(v.replace(/[\s-]/g, "")), {
    message: "Enter a valid mobile number, e.g. 01712345678 or +8801712345678.",
  });

export const fullNameSchema = z
  .string()
  .trim()
  .min(2, "Please enter your full name.")
  .max(120, "That name is too long.");

/** Optional email: empty string is allowed and sent as undefined, anything else must be valid. */
export const optionalEmailSchema = z
  .string()
  .trim()
  .max(190)
  .refine((v) => v === "" || z.string().email().safeParse(v).success, {
    message: "Enter a valid email address, or leave it blank.",
  })
  .transform((v) => (v === "" ? undefined : v))
  .optional();

export const DEGREE_LEVELS = ["BACHELOR", "MASTER", "PHD", "DIPLOMA", "FOUNDATION"] as const;
export const degreeLevelSchema = z.enum(DEGREE_LEVELS);

/* ------------------------------------------------------------------- lead */

const CONTACT_CONSENT_MESSAGE = "We need your permission before a counselor can contact you.";

export const leadSchema = z.object({
  full_name: fullNameSchema,
  phone: phoneSchema,
  email: optionalEmailSchema,
  interest_country: z.string().trim().optional(),
  interest_degree: z.string().trim().optional(),
  interest_subject: z.string().trim().max(160).optional(),
  intake: z.string().trim().optional(),
  message: z.string().trim().max(2000, "Please keep the message under 2000 characters.").optional(),
  // Must be affirmatively true. `boolean().refine` (not `literal(true)`) so form state can start
  // unchecked while the submitted value still has to be true; the API enforces the same rule,
  // so this is a usability check, never the boundary.
  consent_contact: z.boolean().refine((v) => v === true, {
    message: CONTACT_CONSENT_MESSAGE,
  }),
});

export type LeadValues = z.input<typeof leadSchema>;

/* ------------------------------------------------------------ appointment */

// Appointment modes and service types are admin-editable (site_settings.appointment_*), so
// the client cannot enumerate them. It checks only that something was chosen; the API
// validates the value against the list the office currently offers and returns a 400 naming
// the allowed values. Mirrors _validate_booking in app/api/v1/routers/actions.py.

export const appointmentSchema = z.object({
  full_name: fullNameSchema,
  phone: phoneSchema,
  email: optionalEmailSchema,
  service_type: z.string().trim().min(1, "Choose what you would like to discuss."),
  scheduled_date: z
    .string()
    .min(1, "Pick a date.")
    .refine(
      (v) => {
        const picked = new Date(`${v}T00:00:00`);
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        return !Number.isNaN(picked.getTime()) && picked >= today;
      },
      { message: "Choose today or a later date." },
    ),
  scheduled_time: z.string().min(1, "Pick a time slot."),
  mode: z.string().trim().min(1, "Choose how you would like to meet."),
  notes: z.string().trim().max(1000).optional(),
  // Must be affirmatively true. `boolean().refine` (not `literal(true)`) so form state can start
  // unchecked while the submitted value still has to be true; the API enforces the same rule,
  // so this is a usability check, never the boundary.
  consent_contact: z.boolean().refine((v) => v === true, {
    message: CONTACT_CONSENT_MESSAGE,
  }),
});

export type AppointmentValues = z.input<typeof appointmentSchema>;

/* ------------------------------------------------- event registration */

export const eventRegistrationSchema = z.object({
  full_name: fullNameSchema,
  phone: phoneSchema,
  email: optionalEmailSchema,
  preferred_country: z.string().trim().optional(),
  degree_level: z.string().trim().optional(),
  // Registering is a contact request: we message the registrant a reminder.
  // Must be affirmatively true. `boolean().refine` (not `literal(true)`) so form state can start
  // unchecked while the submitted value still has to be true; the API enforces the same rule,
  // so this is a usability check, never the boundary.
  consent_contact: z.boolean().refine((v) => v === true, {
    message: CONTACT_CONSENT_MESSAGE,
  }),
});

export type EventRegistrationValues = z.input<typeof eventRegistrationSchema>;

/* ---------------------------------------------------------- student profile */

/**
 * Bounds here are deliberate: a CGPA or IELTS band outside the possible range is a typo, and
 * catching it in the form is far better than letting the eligibility engine return
 * INFORMATION_UNAVAILABLE for a value the student could have corrected in two seconds.
 */
export const profileSchema = z.object({
  full_name: fullNameSchema.optional(),
  phone: z.string().trim().optional(),
  email: optionalEmailSchema,
  highest_qualification: z.string().trim().optional(),
  cgpa: z.coerce
    .number({ error: "Enter your CGPA as a number." })
    .min(0, "CGPA cannot be negative.")
    .max(5, "CGPA looks too high - use the 4.0 or 5.0 scale.")
    .optional(),
  cgpa_scale: z.coerce.number().min(1).max(100).optional(),
  ielts_overall: z.coerce
    .number({ error: "Enter your IELTS band as a number." })
    .min(0, "IELTS bands run from 0 to 9.")
    .max(9, "IELTS bands run from 0 to 9.")
    .optional(),
  toefl_total: z.coerce.number().min(0).max(120, "TOEFL is scored out of 120.").optional(),
  duolingo: z.coerce.number().min(10).max(160).optional(),
  pte: z.coerce.number().min(10).max(90).optional(),
  gre_total: z.coerce.number().min(260).max(340).optional(),
  gmat_total: z.coerce.number().min(200).max(800).optional(),
  work_experience_years: z.coerce.number().min(0).max(60).optional(),
  publications_count: z.coerce.number().int().min(0).max(999).optional(),
  budget_per_year: z.coerce.number().min(0).optional(),
  budget_currency: z.string().trim().optional(),
  target_degree_level: z.string().trim().optional(),
  target_countries: z.array(z.string()).optional(),
  target_subject: z.string().trim().optional(),
  target_intake: z.string().trim().optional(),
});

export type ProfileValues = z.input<typeof profileSchema>;

/* -------------------------------------------------------------- helpers */

/** Strip "" / undefined / [] so optional fields are omitted rather than sent empty. */
export function compact<T extends Record<string, unknown>>(values: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value === "" || value === undefined || value === null) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    out[key] = value;
  }
  return out as Partial<T>;
}
