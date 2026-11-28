"use client";

import Link from "next/link";
import PortalView from "@/components/portal-view";
import { usePortalIdentity } from "@/lib/use-portal-identity";

/**
 * Token-free portal route.
 *
 * `/my/<token>` is an entry capability only: it verifies the link, stores the identity, and
 * replaces itself with this route. Reading the token from storage here means Back/Forward,
 * refresh and BFCache restoration cannot silently re-authenticate a student from a stale URL.
 */
export default function PortalHome() {
  const token = usePortalIdentity();

  if (token === undefined) {
    return <p className="py-16 text-center text-sm text-slate-500">Loading your portal…</p>;
  }

  if (!token) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-bold text-slate-900">You are signed out</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
          Open your private portal link again, or run the free profile assessment to create one.
        </p>
        <Link href="/profile-assessment"
          className="mt-5 inline-block rounded-xl bg-brand-800 px-5 py-3 text-sm font-bold text-white">
          Run the free profile assessment
        </Link>
      </div>
    );
  }

  return <PortalView token={token} />;
}
