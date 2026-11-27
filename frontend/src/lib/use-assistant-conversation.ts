"use client";

/**
 * The assistant's conversation/session state machine.
 *
 * Kept free of Next.js routing and markup so the privacy rules can be mounted and exercised
 * in a real React tree, while `AssistantWidget` only owns the dialog chrome.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { invalidateAssistantSession } from "./assistant-session.ts";
import {
  identityChanged, identityOf, initialTurns, requestIsCurrent, resetForIdentity, type Turn,
} from "./assistant-lifecycle.ts";
import { getProfileToken, PROFILE_STORAGE_KEY } from "./portal.ts";
import { sessionId, track } from "./track.ts";

type Dict = Record<string, any>;

export function useAssistantConversation(pathname: string) {
  const [turns, setTurns] = useState<Turn[]>(initialTurns);
  const [input, setInput] = useState("");
  const csrfRef = useRef("");
  const sessionPromiseRef = useRef<Promise<void> | null>(null);
  const epochRef = useRef(0);
  const identityRef = useRef(identityOf(getProfileToken()));
  const [busy, setBusy] = useState(false);

  // A real identity change clears every visible trace of the previous student: questions,
  // answers, citations, errors, pending state and any half-typed message. Re-applying the
  // same token is not a transition and deliberately leaves the conversation alone.
  const syncIdentity = useCallback(() => {
    const next = identityOf(getProfileToken());
    if (!identityChanged(identityRef.current, next)) return false;
    identityRef.current = next;
    epochRef.current += 1;
    csrfRef.current = "";
    sessionPromiseRef.current = null;
    const reset = resetForIdentity();
    setTurns(reset.turns);
    setInput(reset.input);
    setBusy(reset.busy);
    return true;
  }, []);

  useEffect(() => {
    const onProfile = () => { syncIdentity(); };
    const onStorage = (event: StorageEvent) => {
      if (event.key === PROFILE_STORAGE_KEY) syncIdentity();
    };
    // Re-read the authoritative token when this tab becomes visible again: a storage
    // event can be delayed while a background tab is throttled.
    const onVisible = () => { if (!document.hidden) syncIdentity(); };
    window.addEventListener("scholara:profile", onProfile);
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("scholara:profile", onProfile);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [syncIdentity]);

  const ensureSession = useCallback(async () => {
    if (csrfRef.current) return;
    if (sessionPromiseRef.current) return sessionPromiseRef.current;

    const run = (async () => {
      syncIdentity();
      const startIdentity = identityRef.current;
      const startEpoch = epochRef.current;
      const guard = () => {
        if (epochRef.current !== startEpoch || identityOf(getProfileToken()) !== startIdentity) {
          throw new Error("profile changed");
        }
      };
      const read = async (response: Response) => response.json().catch(() => ({}));
      const issue = async (profileToken: string | null) =>
        fetch("/api/v1/assistant/session", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(profileToken ? { profile_token: profileToken } : {}),
        });

      let data: Dict | null = null;
      const state = await fetch("/api/v1/assistant/session", {
        credentials: "same-origin",
        cache: "no-store",
      });
      guard();

      if (state.ok) {
        data = await read(state);
        guard();
        const profileToken = getProfileToken();
        if (data?.ownership === "PROFILE" && !profileToken) {
          // Local logout: the cookie still carries Student A. Revoke before anything else.
          await invalidateAssistantSession();
          guard();
          const created = await issue(null);
          guard();
          if (created.ok) data = await read(created);
        } else if (data?.ownership === "PROFILE" && profileToken) {
          // Verify (or explicitly switch) against the token this browser now holds.
          const bound = await fetch("/api/v1/assistant/session/bind", {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": data.csrf_token ?? "",
            },
            body: JSON.stringify({ profile_token: profileToken }),
          });
          guard();
          if (bound.ok) data = await read(bound);
          else {
            // Invalid/replaced token: the server revoked the stale session. Start anonymous.
            data = null;
            const created = await issue(null);
            guard();
            if (created.ok) data = await read(created);
          }
        } else if (data?.ownership === "ANONYMOUS" && profileToken) {
          const bound = await fetch("/api/v1/assistant/session/bind", {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": data.csrf_token ?? "",
            },
            body: JSON.stringify({ profile_token: profileToken }),
          });
          guard();
          if (bound.ok) data = await read(bound);
        }
      } else if (state.status === 401) {
        const created = await issue(getProfileToken());
        guard();
        if (created.ok) data = await read(created);
      }

      // A manual/profile-lifecycle change that did not dispatch an event is still a
      // transition: reset before exposing the new session state.
      if (syncIdentity()) throw new Error("profile changed");
      guard();
      csrfRef.current = data?.csrf_token || "";
    })();

    let tracked: Promise<void>;
    tracked = run.finally(() => {
      // Only the operation that currently owns the ref may clear it. An older bootstrap can
      // finish after a newer identity has established a new promise; it must not erase it.
      if (sessionPromiseRef.current === tracked) sessionPromiseRef.current = null;
    });
    sessionPromiseRef.current = tracked;
    return tracked;
  }, [syncIdentity]);

  const send = useCallback(async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;

    // If the identity changed between typing and submitting, this question belongs to the
    // previous student. Drop it instead of sending A's words under B's new session.
    if (syncIdentity()) return;
    const startIdentity = identityRef.current;
    const startEpoch = epochRef.current;
    const current = () => requestIsCurrent(
      startIdentity, startEpoch, identityOf(getProfileToken()), epochRef.current);

    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { role: "user", text: q }, { role: "assistant", text: "", pending: true }]);
    track("assistant_ask", { path: pathname });

    try {
      await ensureSession().catch(() => undefined);
      if (!current()) throw new Error("profile changed");
      const post = (csrf: string) => fetch("/api/v1/assistant/ask", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": csrf } : {}),
        },
        body: JSON.stringify({
          question: q,
          // The server compares this with the bound session's fingerprint; it never changes
          // ownership from this field alone.
          profile_token: getProfileToken() || undefined,
          // Legacy analytics correlation only; the server does not treat this as a session.
          session_id: sessionId(),
          context: { path: pathname },
        }),
      });
      let res = await post(csrfRef.current);
      if ([401, 403, 409].includes(res.status)) {
        // A response from a superseded student must not clear the newer student's turns,
        // input, CSRF state or session promise, and must not trigger a rebind for them.
        if (!current()) throw new Error("profile changed");
        // The session this conversation belonged to is no longer usable. Clear its visible
        // history before attempting to bootstrap the current identity afresh.
        const reset = resetForIdentity();
        setTurns(reset.turns);
        setInput(reset.input);
        csrfRef.current = "";
        sessionPromiseRef.current = null;
        await ensureSession().catch(() => undefined);
        if (!current()) throw new Error("profile changed");
        res = await post(csrfRef.current);
      }
      const data = await res.json().catch(() => ({}));
      if (!current()) throw new Error("profile changed");
      if (!res.ok) throw new Error(data?.detail ?? "The assistant is unavailable.");
      setTurns((t) => [
        ...t.slice(0, -1),
        {
          role: "assistant", text: data.answer ?? "", sources: data.sources ?? [],
          suggestions: data.suggestions ?? [], grounded: data.grounded, escalated: data.escalated,
        },
      ]);
    } catch (e: any) {
      // A stale success or stale failure must never append anything after a transition.
      if (!current()) {
        syncIdentity();
        return;
      }
      setTurns((t) => [
        ...t.slice(0, -1),
        {
          role: "assistant", grounded: false,
          text: e?.message?.includes("Too many")
            ? "You have asked a lot of questions in a short time. Give it a minute, or message a counselor on WhatsApp."
            : "I could not reach the answer service just now. Please try again, or message a counselor on WhatsApp.",
        },
      ]);
    } finally {
      if (current()) setBusy(false);
    }
  }, [busy, pathname, syncIdentity]);

  return { turns, input, setInput, busy, send };
}
