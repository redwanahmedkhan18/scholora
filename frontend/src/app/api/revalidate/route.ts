import { revalidatePath, revalidateTag } from "next/cache";
import { resolveApiBase } from "../../../../config/api-base";

/**
 * On-demand revalidation for content an administrator just changed.
 *
 * Next caches the settings fetch for 300 seconds and every page that prints a phone number,
 * email address, WhatsApp link or Facebook link is rendered from it. An administrator saving a
 * new number therefore saw it appear minutes later - or, on a prerendered page, only after the
 * next deploy. This route is the missing signal: the admin form calls it straight after a
 * successful save.
 *
 * Authorization is not invented here. The caller's bearer token is forwarded to the API's own
 * `/admin/auth/me`, and only a real staff session may flush the cache. The route reads its
 * upstream origin from the same validated `INTERNAL_API_URL` every server component uses.
 */

const ALLOWED_ROLES = new Set([
  "ADMIN", "SENIOR_COUNSELOR", "DATA_MANAGER", "APPLICATION_OFFICER", "COUNSELOR",
]);

const PATHS = ["/", "/contact"];

export async function POST(request: Request) {
  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.toLowerCase().startsWith("bearer ")) {
    return Response.json({ detail: "Sign in again to publish changes." }, { status: 401 });
  }

  let base: string;
  try {
    base = resolveApiBase();
  } catch {
    return Response.json({ detail: "The API is not configured for this deployment." }, { status: 503 });
  }

  let role: string | null = null;
  try {
    const res = await fetch(`${base}/api/v1/admin/auth/me`, {
      headers: { Authorization: authorization, Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) {
      return Response.json({ detail: "Your session is no longer valid." }, { status: 401 });
    }
    role = (await res.json())?.role ?? null;
  } catch {
    // The page has already been saved; failing here must not look like a failed save. The
    // caller treats 503 as "published, cache refresh pending" and says so.
    return Response.json({ detail: "The API did not answer; the cache will refresh on its own." },
      { status: 503 });
  }

  if (!role || !ALLOWED_ROLES.has(role)) {
    return Response.json({ detail: "Insufficient permissions." }, { status: 403 });
  }

  // Next 16 requires a cache profile: "max" serves the cached copy while it refreshes, so a
  // burst of visitors after a save does not stampede the API.
  revalidateTag("site-settings", "max");
  for (const path of PATHS) revalidatePath(path);

  return Response.json({ revalidated: true, paths: PATHS, at: new Date().toISOString() });
}
