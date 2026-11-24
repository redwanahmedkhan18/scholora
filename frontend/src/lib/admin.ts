"use client";

type Dict = Record<string, any>;

const TOKEN_KEY = "scholara.admin.token";
const USER_KEY = "scholara.admin.user";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function getUser(): Dict | null {
  if (typeof window === "undefined") return null;
  try { return JSON.parse(window.localStorage.getItem(USER_KEY) ?? "null"); } catch { return null; }
}
export function setSession(token: string, user: Dict) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event("scholara:admin-auth"));
}
export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event("scholara:admin-auth"));
}

export class AuthError extends Error {}

export async function adminFetch<T = Dict>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const res = await fetch(`/api/v1/admin${path}`, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (res.status === 401 || res.status === 403) {
    if (res.status === 401) clearSession();
    const body = await res.json().catch(() => ({}));
    throw new AuthError(body?.detail ?? "Your session expired. Please sign in again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`);
  return data as T;
}
