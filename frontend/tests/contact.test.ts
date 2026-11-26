/**
 * Contact-channel unit tests.
 *
 * These run with `node --test` (no bundler, no jsdom): `contact.ts` is deliberately pure so the
 * link builders can be tested directly. Each case below is a real defect that shipped at some
 * point - a `tel:` link with spaces in it, a `wa.me` link with a leading `+`, an address that
 * silently pointed somewhere else.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  contactFromSettings,
  formatPhone,
  mailtoHref,
  phoneDigits,
  safeExternalHref,
  telHref,
  validateContactForm,
  whatsappHref,
} from "../src/lib/contact.ts";

test("phoneDigits keeps only the digits", () => {
  assert.equal(phoneDigits("+880 1711-223344"), "8801711223344");
  assert.equal(phoneDigits("(02) 5566 7788"), "0255667788");
  assert.equal(phoneDigits(null), "");
  assert.equal(phoneDigits(undefined), "");
});

test("telHref keeps the plus and drops the punctuation", () => {
  assert.equal(telHref("+880 1711-223344"), "tel:+8801711223344");
  assert.equal(telHref("01711223344"), "tel:01711223344");
});

test("telHref refuses anything that is not dialable", () => {
  assert.equal(telHref(""), null);
  assert.equal(telHref("12345"), null);
  assert.equal(telHref("X"), null);
  assert.equal(telHref(null), null);
});

test("whatsappHref uses digits only - wa.me rejects a leading plus", () => {
  const href = whatsappHref("+880 1711-223344", "Hello");
  assert.ok(href);
  assert.match(href, /^https:\/\/wa\.me\/8801711223344\?text=/);
  assert.ok(!href.includes("+"));
});

test("whatsappHref falls back to the default greeting", () => {
  const href = whatsappHref("8801711223344", "");
  assert.ok(href && href.includes("text=Hello"));
});

test("whatsappHref only appends a context when one is given", () => {
  const plain = whatsappHref("8801711223344", "Hi");
  const ctx = whatsappHref("8801711223344", "Hi", "(page: /programs)");
  assert.ok(plain && ctx);
  assert.ok(!plain.includes("page:"));
  assert.ok(ctx.includes(encodeURIComponent("(page: /programs)")));
});

test("mailtoHref rejects a display label passed in by mistake", () => {
  assert.equal(mailtoHref("info@scholara.com.bd"), "mailto:info@scholara.com.bd");
  assert.equal(mailtoHref("info at scholara"), null);
  assert.equal(mailtoHref("+8801711223344"), null);
});

test("safeExternalHref refuses javascript: and data: URLs", () => {
  assert.equal(safeExternalHref("https://www.facebook.com/scholara"), "https://www.facebook.com/scholara");
  assert.equal(safeExternalHref("javascript:alert(1)"), null);
  assert.equal(safeExternalHref("data:text/html,<script>alert(1)</script>"), null);
  assert.equal(safeExternalHref("  "), null);
});

test("formatPhone groups a Bangladeshi number", () => {
  assert.equal(formatPhone("+8801711223344"), "+880 1711-223344");
  // Anything else is printed as stored rather than mangled.
  assert.equal(formatPhone("+44 20 7946 0958"), "+44 20 7946 0958");
});

test("contactFromSettings reads the flattened settings shape", () => {
  const c = contactFromSettings({
    phones: ["+8801711223344"],
    emails: ["info@scholara.com.bd"],
    whatsapp_number: "+8801711223344",
    whatsapp_default_message: "Hi there",
    facebook_page_url: "https://www.facebook.com/scholara",
  });
  assert.equal(c.telHref, "tel:+8801711223344");
  assert.equal(c.mailtoHref, "mailto:info@scholara.com.bd");
  assert.equal(c.facebookHref, "https://www.facebook.com/scholara");
  assert.equal(c.whatsappMessage, "Hi there");
  assert.ok(c.whatsappHref?.startsWith("https://wa.me/8801711223344"));
});

test("contactFromSettings drops placeholders instead of publishing them", () => {
  const c = contactFromSettings({
    phones: ["+880 1700-000000"],
    emails: ["info@scholara.com.bd"],
    whatsapp_number: "88017XXXXXXXX",
  });
  // The seeded placeholder is all zero digits: show no phone rather than a dead line.
  assert.equal(c.phone, null);
  assert.equal(c.telHref, null);
  assert.equal(c.whatsappHref, null);
  assert.equal(c.email, "info@scholara.com.bd");
});

test("contactFromSettings hides WhatsApp when the switch is off", () => {
  const c = contactFromSettings({ whatsapp_enabled: false, whatsapp_number: "+8801711223344" });
  assert.equal(c.whatsappHref, null);
  assert.equal(c.whatsappEnabled, false);
});

test("contactFromSettings survives a missing or empty settings object", () => {
  for (const input of [null, undefined, {}]) {
    const c = contactFromSettings(input as any);
    assert.equal(c.telHref, null);
    assert.equal(c.mailtoHref, null);
    assert.equal(c.whatsappHref, null);
    assert.equal(c.facebookHref, null);
    assert.ok(c.whatsappMessage.length > 0);
  }
});

test("contactFromSettings prefers a real value over a placeholder in a list", () => {
  const c = contactFromSettings({ phones: ["+880 1700-000000", "+8801711223344"] });
  assert.equal(c.phone, "+8801711223344");
  assert.equal(c.phoneDisplay, "+880 1711-223344");
});

test("validateContactForm accepts a normal set of values", () => {
  assert.deepEqual(
    validateContactForm({
      primaryPhone: "+8801711223344",
      whatsappNumber: "01711223344",
      email: "info@scholara.com.bd",
      facebookUrl: "https://www.facebook.com/scholara",
      extraPhones: "",
      extraEmails: "",
    }),
    {},
  );
});

test("validateContactForm explains every bad field", () => {
  const errors = validateContactForm({
    primaryPhone: "abc",
    whatsappNumber: "",
    email: "info@",
    facebookUrl: "javascript:alert(1)",
    extraPhones: "",
    extraEmails: "",
  });
  assert.ok(errors.primaryPhone);
  assert.ok(errors.whatsappNumber);
  assert.ok(errors.email);
  assert.ok(errors.facebookUrl);
});

test("validateContactForm rejects a non-Facebook link", () => {
  const errors = validateContactForm({
    primaryPhone: "+8801711223344",
    whatsappNumber: "+8801711223344",
    email: "info@scholara.com.bd",
    facebookUrl: "https://example.com/phishing",
    extraPhones: "",
    extraEmails: "",
  });
  assert.match(errors.facebookUrl ?? "", /facebook\.com/);
});

test("contactFromSettings hides a legacy row that is not a phone number", () => {
  // These exact strings are what the audit wrote into the dev database through the old
  // unvalidated PUT /admin/settings. They must not become a link or a printed number.
  const c = contactFromSettings({
    phones: ["not-a-phone"],
    emails: ["nope"],
    whatsapp_number: "abc",
    facebook_page_url: "javascript:alert(1)",
  });
  assert.equal(c.phone, null);
  assert.equal(c.telHref, null);
  assert.equal(c.email, null);
  assert.equal(c.mailtoHref, null);
  assert.equal(c.whatsappHref, null);
  assert.equal(c.facebookHref, null);
});

test("contactFromSettings skips a junk entry and uses the next good one", () => {
  const c = contactFromSettings({ phones: ["not-a-phone", "+8801711223344"], emails: ["nope", "hello@scholara.com.bd"] });
  assert.equal(c.telHref, "tel:+8801711223344");
  assert.equal(c.email, "hello@scholara.com.bd");
});

test("validateContactForm rejects an unusable extra channel but allows blank lines", () => {
  const base = {
    primaryPhone: "+8801711223344",
    whatsappNumber: "+8801711223344",
    email: "info@scholara.com.bd",
    facebookUrl: "https://www.facebook.com/scholara",
  };
  assert.deepEqual(validateContactForm({ ...base, extraPhones: "+8801911223344\n\n", extraEmails: "" }), {});
  const errors = validateContactForm({ ...base, extraPhones: "call the office\n", extraEmails: "second@" });
  assert.match(errors.extraPhones ?? "", /not a phone number/);
  assert.match(errors.extraEmails ?? "", /not an email address/);
});
