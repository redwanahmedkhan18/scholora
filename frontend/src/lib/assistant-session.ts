/**
 * W4 assistant-session lifecycle used by the portal.
 *
 * The raw session credential lives only in an HttpOnly cookie and is never readable here.
 * These helpers only revoke server-side ownership when the student profile changes or is
 * cleared, so an old profile-bound session cannot keep answering with the previous student's
 * data.
 */

const SESSION_STATE = "/api/v1/assistant/session";
const SESSION_REVOKE = "/api/v1/assistant/session/revoke";

/**
 * Revoke the current assistant session, best effort.
 *
 * Reads the non-sensitive CSRF token from the same-origin state endpoint, then revokes. A
 * missing session is already safe. Failures are swallowed because the server-side profile
 * fingerprint check also rejects a stale session on the next ask.
 */
export async function invalidateAssistantSession(): Promise<void> {
  if (typeof window === "undefined") return;
  try {
    const state = await fetch(SESSION_STATE, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!state.ok) return;
    const data = await state.json().catch(() => ({}));
    if (!data?.csrf_token) return;
    await fetch(SESSION_REVOKE, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": data.csrf_token },
    });
  } catch {
    // Offline/aborted: the next bootstrap will replace the session, and the server rejects
    // a mismatched profile fingerprint regardless.
  }
}
