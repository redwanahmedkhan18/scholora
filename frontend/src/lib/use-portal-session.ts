"use client";

/**
 * Portal data lifecycle. Kept separate from the Next.js route so its generation, abort and
 * logout rules can be mounted in a real React tree.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  clearProfileToken, getIdentityRevision, getProfileToken, IDENTITY_REVISION_KEY,
  PROFILE_STORAGE_KEY, portalFetch, setProfileToken, syncShortlist,
} from "./portal.ts";

type Dict = Record<string, any>;

type Options = {
  consumeToken?: boolean;
  onTokenConsumed?: () => void;
  onSignedOut?: () => void;
};

export function usePortalSession(token: string, options: Options = {}) {
  const { consumeToken = false, onTokenConsumed, onSignedOut } = options;
  const [loaded, setLoaded] = useState<{ token: string; data: Dict } | null>(null);
  const [errorState, setErrorState] = useState<{ token: string; message: string } | null>(null);
  const generationRef = useRef(0);
  const signedOutRef = useRef(false);
  const mountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const signOut = useCallback(() => {
    // Invalidate every pending load/sync synchronously before touching storage. A response
    // that is already in flight cannot regain authority after this generation change.
    signedOutRef.current = true;
    generationRef.current += 1;
    abortRef.current?.abort();
    clearProfileToken();
    setLoaded(null);
    setErrorState(null);
    onSignedOut?.();
  }, [onSignedOut]);

  const load = useCallback(async () => {
    if (signedOutRef.current) return;
    const generation = ++generationRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    // Capture both the identity and the browser-wide transition revision. A token-entry
    // request may only apply its response while neither has moved, even if another tab
    // changed identity while this request was in flight.
    const startStored = getProfileToken();
    const startRevision = getIdentityRevision();
    const active = () => mountedRef.current && !signedOutRef.current
      && generationRef.current === generation
      && getProfileToken() === startStored
      && getIdentityRevision() === startRevision;
    try {
      const res = await portalFetch<Dict>(`/portal/${token}`, { signal: controller.signal });
      if (!active()) return;
      setLoaded({ token, data: res });
      if (!active()) return;
      setProfileToken(token, res?.profile?.full_name);
      // The write above is this request's own identity transition; it is synchronous and
      // cannot interleave with another tab. Do not re-check the revision it just advanced.
      setErrorState(null);
      // Consume the bearer token back into browser storage and leave a token-free route in
      // history. Back/Forward can no longer restore `/my/<token>`.
      if (consumeToken) onTokenConsumed?.();
    } catch (e: any) {
      if (!active()) return;
      setErrorState({ token, message: e?.message ?? "We could not load this portal." });
    }
  }, [token, consumeToken, onTokenConsumed]);

  useEffect(() => {
    signedOutRef.current = false;
    setLoaded(null);
    setErrorState(null);
    void load();
    const onPageShow = (event: PageTransitionEvent) => {
      // A BFCache-restored token-entry page must not re-authenticate from the old URL after
      // the browser has signed out or moved to another student.
      const stale = consumeToken
        ? event.persisted && getProfileToken() !== token
        : getProfileToken() !== token;
      if (stale) {
        generationRef.current += 1;
        abortRef.current?.abort();
        setLoaded(null);
        onSignedOut?.();
      }
    };
    window.addEventListener("pageshow", onPageShow);
    const startStored = getProfileToken();
    const startRevision = getIdentityRevision();
    const onStorage = (event: StorageEvent) => {
      if (event.key !== PROFILE_STORAGE_KEY && event.key !== IDENTITY_REVISION_KEY
          && event.key !== null) return;
      if (getProfileToken() === startStored && getIdentityRevision() === startRevision) return;
      generationRef.current += 1;
      abortRef.current?.abort();
      setLoaded(null);
      if (getProfileToken() === null) onSignedOut?.();
    };
    window.addEventListener("storage", onStorage);
    // Anonymous shortlists are never automatically attributed to this profile; the scoped
    // buckets keep students separate.
    void syncShortlist(token).then((added) => { if (added > 0) void load(); });
    return () => {
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("storage", onStorage);
      abortRef.current?.abort();
    };
  }, [load, token, consumeToken, onSignedOut]);

  return {
    data: loaded && loaded.token === token
      && (getProfileToken() === token || consumeToken) ? loaded.data : null,
    error: errorState && errorState.token === token ? errorState.message : "",
    load,
    signOut,
  };
}
