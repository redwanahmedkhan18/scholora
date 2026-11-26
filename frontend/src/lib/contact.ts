/**
 * Contact channels: one shape, one set of link builders, no duplicated string surgery.
 *
 * The header prints a phone number, the footer prints it again, the mobile navigation draws a
 * WhatsApp button and the contact page prints all four channels. Before this module each of
 * them built its own `tel:`/`wa.me`/`mailto:` string inline, so a value that arrived without
 * its country code - or with spaces in it - produced a broken link in one place and a working
 * one in another.
 *
 * Everything here is pure and dependency-free so it can be unit-tested with `node --test`.
 */

export type Contact = {
  /** The stored values, normalised for display. */
  phone: string | null;
  phoneDisplay: string | null;
  email: string | null;
  whatsapp: string | null;
  whatsappMessage: string;
  whatsappEnabled: boolean;
  facebookUrl: string | null;
  /** Ready-to-use targets. Null when the channel is missing or unusable, so callers can hide it. */
  telHref: string | null;
  mailtoHref: string | null;
  whatsappHref: string | null;
  facebookHref: string | null;
};

type Settings = Record<string, any>;

const DIGITS = /\D+/g;
const DEFAULT_WHATSAPP_MESSAGE = "Hello Scholara, I would like to discuss studying abroad.";

/** `+8801711223344` from any punctuation an administrator (or a legacy row) may contain. */
export function phoneDigits(value: unknown): string {
  return String(value ?? "").replace(DIGITS, "");
}

/** A `tel:` target, or null when there is nothing dialable. E.164 keeps `+`. */
export function telHref(phone: unknown): string | null {
  const raw = String(phone ?? "").trim();
  const digits = phoneDigits(raw);
  if (digits.length < 6) return null;
  return `tel:${raw.startsWith("+") ? "+" : ""}${digits}`;
}

/** A `mailto:` target, or null. Guards against a display label being passed in by mistake. */
export function mailtoHref(email: unknown): string | null {
  const value = String(email ?? "").trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return null;
  return `mailto:${value}`;
}

/** A `wa.me` deep link. `wa.me` requires digits only, never a leading plus or separator. */
export function whatsappHref(number: unknown, message?: string | null, context?: string | null): string | null {
  const digits = phoneDigits(number);
  if (digits.length < 6) return null;
  const text = [message || DEFAULT_WHATSAPP_MESSAGE, context || ""].filter(Boolean).join(" ");
  return `https://wa.me/${digits}?text=${encodeURIComponent(text)}`;
}

/** `https://…` for a outbound social/page link, rejecting anything that is not http(s). */
export function safeExternalHref(url: unknown): string | null {
  const value = String(url ?? "").trim();
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

/**
 * Read the four public channels out of the settings object the API returns.
 *
 * Tolerates both the flattened shape (`/settings`) and the grouped shape it wraps, and treats
 * obvious placeholders as "not configured" so the interface hides a channel rather than
 * publishing a number nobody answers.
 */
export function contactFromSettings(settings: Settings | null | undefined): Contact {
  const s = settings ?? {};
  const whatsapp = s.whatsapp ?? {};
  const facebook = s.facebook ?? {};

  const phone = firstDialable(s.phones) ?? null;
  const email = firstEmailable(s.emails) ?? null;
  const whatsappNumber = firstDialable([s.whatsapp_number ?? whatsapp.number]) ?? null;
  const facebookUrl = safeExternalHref(s.facebook_page_url ?? facebook.page_url);

  return {
    phone,
    phoneDisplay: phone ? formatPhone(phone) : null,
    email,
    whatsapp: whatsappNumber,
    whatsappMessage: String(s.whatsapp_default_message ?? whatsapp.default_message ?? "").trim()
      || DEFAULT_WHATSAPP_MESSAGE,
    whatsappEnabled: (s.whatsapp_enabled ?? whatsapp.enabled ?? true) !== false,
    facebookUrl,
    telHref: telHref(phone),
    mailtoHref: mailtoHref(email),
    whatsappHref: (s.whatsapp_enabled ?? whatsapp.enabled ?? true) === false
      ? null
      : whatsappHref(whatsappNumber, s.whatsapp_default_message ?? whatsapp.default_message),
    facebookHref: facebookUrl,
  };
}

/** A value that is really there: not blank, not an `XXXX` placeholder, not a run of zeroes. */
function firstUsable(input: unknown): string | null {
  const values: unknown[] = Array.isArray(input) ? input : [input];
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (!text) continue;
    if (/X{4,}/i.test(text)) continue;
    const digits = phoneDigits(text);
    // "+880 1700-000000" is the placeholder the seed ships with: nobody answers it, so it must
    // not be published as though someone does. A run of six zeroes is not a real number.
    if (digits && (/^0+$/.test(digits) || /0{6,}/.test(digits))) continue;
    return text;
  }
  return null;
}

/**
 * The first value that can actually be dialled.
 *
 * A settings row written before the admin form validated its input can still contain anything
 * at all ("not-a-phone", "abc"). Those must not reach the page: `tel:not-a-phone` is a dead
 * link and the string itself is not a phone number, so the channel is simply hidden.
 */
function firstDialable(input: unknown): string | null {
  const values = Array.isArray(input) ? input : [input];
  for (const value of firstValues(values)) {
    if (phoneDigits(value).length >= 6) return value;
  }
  return null;
}

/** The first value that is an email address. */
function firstEmailable(input: unknown): string | null {
  const values = Array.isArray(input) ? input : [input];
  for (const value of firstValues(values)) {
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return value;
  }
  return null;
}

/** Every candidate that survived the placeholder filter, in order. */
function firstValues(values: unknown[]): string[] {
  const out: string[] = [];
  for (const value of values) {
    const usable = firstUsable([value]);
    if (usable) out.push(usable);
  }
  return out;
}

/** Group a number for display without changing the value used in links. */
export function formatPhone(value: unknown): string {
  const raw = String(value ?? "").trim();
  const digits = phoneDigits(raw);
  if (digits.startsWith("880") && digits.length === 13) {
    return `+880 ${digits.slice(3, 7)}-${digits.slice(7)}`;
  }
  return raw;
}

/** Client-side mirror of the server rules, so a typo is caught before a round trip. */
export type ContactFormValues = {
  primaryPhone: string;
  whatsappNumber: string;
  email: string;
  facebookUrl: string;
  /** One entry per line; blank lines are ignored. */
  extraPhones: string;
  extraEmails: string;
};

export type ContactFormErrors = Partial<Record<keyof ContactFormValues, string>>;

export function validateContactForm(values: ContactFormValues): ContactFormErrors {
  const errors: ContactFormErrors = {};
  const phone = phoneDigits(values.primaryPhone);
  if (!phone) {
    errors.primaryPhone = "Enter the number that should appear in the header.";
  } else if (!/^\+?\d{8,15}$/.test(values.primaryPhone.trim())) {
    errors.primaryPhone = "Use an international number, e.g. +8801711223344.";
  }

  const whatsapp = phoneDigits(values.whatsappNumber);
  if (!whatsapp) {
    errors.whatsappNumber = "Enter the WhatsApp number (it may be the same as the main number).";
  } else if (!/^\+?\d{8,15}$/.test(values.whatsappNumber.trim())) {
    errors.whatsappNumber = "WhatsApp needs an international number, digits only.";
  }

  if (!values.email.trim()) {
    errors.email = "Enter the address enquiries should go to.";
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim())) {
    errors.email = "That does not look like an email address.";
  }

  const facebook = safeExternalHref(values.facebookUrl);
  if (!facebook) {
    errors.facebookUrl = "Use the full https:// link to your Facebook page.";
  } else if (!/(^|\.)(facebook\.com|fb\.com|fb\.me|messenger\.com)$/i.test(new URL(facebook).hostname)) {
    errors.facebookUrl = "That is not a facebook.com link.";
  }

  // Extra channels are optional, but a line that is present must be usable - a second number
  // that cannot be dialled is worse than no second number, because it is printed in the footer.
  const badPhone = values.extraPhones.split("\n").map((s) => s.trim()).filter(Boolean)
    .find((line) => !/^\+?\d{8,15}$/.test(line));
  if (badPhone) errors.extraPhones = `"${badPhone}" is not a phone number. Use +8801711223344.`;

  const badEmail = values.extraEmails.split("\n").map((s) => s.trim()).filter(Boolean)
    .find((line) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(line));
  if (badEmail) errors.extraEmails = `"${badEmail}" is not an email address.`;

  return errors;
}
