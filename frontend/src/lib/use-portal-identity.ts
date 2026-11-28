"use client";

/**
 * Identity gate for the token-free `/my` route.
 *
 * `/my/<token>` is only an entry capability. This hook reads the stored identity and re-reads
 * it on storage, focus, visibility and BFCache `pageshow`, so a restored page cannot keep
 * showing a student who has signed out or switched away.
 */
import { useEffect, useState } from "react";
import { getProfileToken, PROFILE_STORAGE_KEY } from "./portal.ts";

export function usePortalIdentity() {
  const [token, setToken] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    const read = () => setToken(getProfileToken());
    read();
    const onStorage = (event: StorageEvent) => {
      if (event.key === PROFILE_STORAGE_KEY || event.key === null) read();
    };
    const onPageShow = () => read();
    const onVisible = () => { if (!document.hidden) read(); };
    window.addEventListener("storage", onStorage);
    window.addEventListener("pageshow", onPageShow);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("pageshow", onPageShow);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return token;
}
