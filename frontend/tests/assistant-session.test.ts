/**
 * W4 profile-lifecycle guards.
 *
 * The security boundary is server-side, but the browser must not keep using Student A's
 * assistant session after the stored profile changes, and must not lose the cross-tab case.
 * These are source assertions, matching the approach used for the admin login guards.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const portal = readFileSync(join(here, "..", "src", "lib", "portal.ts"), "utf8");
const assistant = readFileSync(join(here, "..", "src", "components", "assistant.tsx"), "utf8");
const session = readFileSync(join(here, "..", "src", "lib", "assistant-session.ts"), "utf8");
const portalView = readFileSync(join(here, "..", "src", "components", "portal-view.tsx"), "utf8");
const assistantHook = readFileSync(join(here, "..", "src", "lib", "use-assistant-conversation.ts"), "utf8");
const portalSession = readFileSync(join(here, "..", "src", "lib", "use-portal-session.ts"), "utf8");
const portalHome = readFileSync(join(here, "..", "src", "components", "portal-home.tsx"), "utf8");

test("leaving or replacing a profile revokes the assistant session", () => {
  assert.match(portal, /invalidateAssistantSession/);
  assert.match(portal, /if \(replaced\) \{[\s\S]*bumpIdentityRevision[\s\S]*invalidateAssistantSession/);
  assert.match(portal, /removeItem\(TOKEN_KEY\)[\s\S]*invalidateAssistantSession/);
});

test("profile changes are propagated across tabs through the storage event", () => {
  assert.match(portal, /addEventListener\("storage"/);
  assert.match(portal, /event\.key === TOKEN_KEY/);
  assert.match(assistantHook, /PROFILE_STORAGE_KEY/);
  assert.match(assistantHook, /addEventListener\("storage"/);
});

test("a profile transition invalidates in-flight session state", () => {
  assert.match(assistantHook, /epochRef/);
  assert.match(assistantHook, /profile changed/);
  assert.match(assistantHook, /sessionPromiseRef/);
});

test("the current profile token is sent for server-side comparison, never as authority", () => {
  assert.match(assistantHook, /profile_token: getProfileToken\(\)/);
  assert.match(assistantHook, /\[401, 403, 409\]/);
});

test("the raw assistant credential is never put in browser storage", () => {
  assert.ok(!/localStorage|sessionStorage/.test(session),
    "assistant-session.ts must not persist the session value");
  assert.ok(!/localStorage\.setItem\([^)]*csrf/.test(assistant),
    "CSRF/session material must not be written to localStorage");
});

test("the portal exposes a real sign-out action wired to the clearing lifecycle", () => {
  assert.match(portalSession, /clearProfileToken/);
  assert.match(portalSession, /const signOut = useCallback/);
  assert.match(portalView, /onClick={signOut}/);
  assert.match(portalHome, /usePortalIdentity/);
});

test("the widget wires the identity rules into every answer path", () => {
  assert.match(assistantHook, /syncIdentity/);
  assert.match(assistantHook, /requestIsCurrent/);
  assert.match(assistantHook, /resetForIdentity/);
  assert.match(assistantHook, /if \(!current\(\)\)/);
});
