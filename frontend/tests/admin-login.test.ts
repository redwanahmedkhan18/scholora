/**
 * Guards on the staff sign-in screen.
 *
 * The production login page used to print the seeded administrator's email and password and
 * tell the reader to run a helper script for a second factor. Two things must now be true:
 *
 *  1. no credential or helper-script instruction is rendered outside a development-only
 *     branch, and
 *  2. the second factor is a real challenge, not a re-post of the password.
 *
 * These are source assertions on purpose: the defect was in the source, and a rendering test
 * would need a browser to prove that a compile-time `NODE_ENV` branch disappears.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const shell = readFileSync(join(here, "..", "src", "components", "admin", "shell.tsx"), "utf8");

/** Text of the `DevLoginHint` component, which is the only place dev credentials may appear. */
function devHintBlock(source: string): string {
  const start = source.indexOf("function DevLoginHint()");
  assert.ok(start > 0, "DevLoginHint component is missing");
  return source.slice(start);
}

test("the seeded password appears only inside the development-only hint", () => {
  const hint = devHintBlock(shell);
  const outside = shell.replace(hint, "");
  assert.ok(!outside.includes("Admin@12345"), "seeded password rendered outside DevLoginHint");
  assert.ok(!outside.includes("admin@scholara.com.bd"), "seeded email rendered outside DevLoginHint");
});

test("the development hint is compiled out of production builds", () => {
  const hint = devHintBlock(shell);
  // A compile-time NODE_ENV check is eliminated by the bundler; CSS hiding or a runtime
  // feature flag would leave the text in the shipped JavaScript.
  assert.match(
    hint,
    /if \(process\.env\.NODE_ENV !== "development"\) return null;/,
    "DevLoginHint must return null at build time when NODE_ENV is not development",
  );
});

test("the helper script is never referenced from the sign-in screen", () => {
  assert.ok(!shell.includes("mfa-code.sh"), "scripts/mfa-code.sh is still referenced in the UI");
});

test("step two uses a challenge token, not the password", () => {
  // The old code posted { email, password, code } to the challenge endpoint, which let a
  // leaked password alone reach the verification step on a fresh request.
  assert.match(shell, /challenge_token: challenge/);
  assert.ok(!/body: JSON\.stringify\(\{ email, password, code \}\)/.test(shell));
  assert.match(shell, /\/api\/v1\/admin\/auth\/mfa\/challenge/);
});

test("a lost authenticator can be recovered in the UI", () => {
  assert.match(shell, /\/api\/v1\/admin\/auth\/mfa\/recover/);
  assert.match(shell, /Lost your authenticator\?/);
  assert.match(shell, /recovery_code/);
});

test("the enrolment screen shows a scannable QR code and the manual key", () => {
  assert.match(shell, /qr_data_uri/);
  assert.match(shell, /setup key/i);
});

test("the session is only stored after the second factor succeeds", () => {
  // setSession must not run in the mfa_required branch - the challenge token is not a session.
  const requiredBranch = shell.slice(
    shell.indexOf("if (data.mfa_required)"),
    shell.indexOf("if (data.mfa_enrolment_required)"),
  );
  assert.ok(requiredBranch.length > 0);
  assert.ok(!requiredBranch.includes("setSession("), "a session was created before the code was verified");
});

test("the security panel cannot leak a secret into the page title or a query string", () => {
  const workflow = readFileSync(join(here, "..", "src", "components", "admin", "workflow.tsx"), "utf8");
  assert.ok(!/mfa\/enroll\?secret=/.test(workflow));
  assert.match(workflow, /qr_data_uri/);
  assert.match(workflow, /recovery-codes\/regenerate/);
});
