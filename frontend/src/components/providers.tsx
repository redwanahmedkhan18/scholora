"use client";

/**
 * TanStack Query provider.
 *
 * The admin console is a set of dashboards that poll the same endpoints from several panels at
 * once (the operations page alone loads jobs, conversations and providers side by side). The
 * previous `useResource` hook refetched on every mount with no cache and no dedupe, so moving
 * between tabs re-hit the API each time and two panels asking for the same path issued two
 * requests. Query gives dedupe, a short stale window, and background refresh on focus.
 *
 * `AuthError` (a 401 from `adminFetch`) must never be retried - the token is gone and retrying
 * just burns the login rate limit - so `retry` inspects the error first.
 */

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthError } from "@/lib/admin";

export function Providers({ children }: { children: React.ReactNode }) {
  // Created in state so each browser session gets exactly one client, and so the client is never
  // shared between users during SSR.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: true,
            refetchOnReconnect: true,
            retry: (failureCount, error) => {
              if (error instanceof AuthError) return false;
              const status = (error as { status?: number } | null)?.status;
              if (status && status >= 400 && status < 500 && status !== 429) return false;
              return failureCount < 2;
            },
            retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
          },
          mutations: { retry: false },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export default Providers;
