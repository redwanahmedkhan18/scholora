"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { adminFetch, clearSession, getToken, getUser, setSession } from "@/lib/admin";
import { RESOURCE_GROUPS, RESOURCE_META } from "@/lib/admin-meta";

type Dict = Record<string, any>;

export default function AdminShell({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<Dict | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    const sync = () => setUser(getToken() ? getUser() : null);
    sync();
    setReady(true);
    window.addEventListener("scholara:admin-auth", sync);
    return () => window.removeEventListener("scholara:admin-auth", sync);
  }, []);

  useEffect(() => { setNavOpen(false); }, [pathname]);

  if (!ready) return <div className="grid min-h-screen place-items-center text-sm text-slate-500">Loading…</div>;
  if (!user) return <Login />;

  const grouped = RESOURCE_GROUPS.map((g) => ({
    group: g,
    items: Object.entries(RESOURCE_META).filter(([, m]) => m.group === g),
  }));

  return (
    <div className="flex min-h-screen bg-slate-100">
      <aside className={`${navOpen ? "block" : "hidden"} fixed inset-y-0 left-0 z-40 w-72 overflow-y-auto bg-brand-950 p-4 text-brand-100 lg:sticky lg:top-0 lg:block lg:h-screen`}>
        <Link href="/admin" className="flex items-center gap-2 px-2 py-2">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-white text-sm font-black text-brand-900">S</span>
          <span>
            <span className="block text-sm font-black text-white">Scholara Admin</span>
            <span className="block text-[10px] uppercase tracking-wider text-accent-400">Control centre</span>
          </span>
        </Link>

        <nav className="mt-4 space-y-5">
          <div>
            <p className="px-2 text-[10px] font-bold uppercase tracking-[0.16em] text-brand-100/60">Overview</p>
            <ul className="mt-1.5 space-y-0.5">
              <NavLink href="/admin" label="Dashboard" active={pathname === "/admin"} />
              <NavLink href="/admin/counselor" label="My desk" active={pathname === "/admin/counselor"} />
              <NavLink href="/admin/marketing" label="Marketing & ads" active={pathname === "/admin/marketing"} />
              <NavLink href="/admin/settings" label="Site settings" active={pathname === "/admin/settings"} />
            </ul>
          </div>

          <div>
            <p className="px-2 text-[10px] font-bold uppercase tracking-[0.16em] text-brand-100/60">Student workflow</p>
            <ul className="mt-1.5 space-y-0.5">
              <NavLink href="/admin/applications" label="Applications" active={pathname === "/admin/applications"} />
              <NavLink href="/admin/documents" label="Document review" active={pathname === "/admin/documents"} />
              <NavLink href="/admin/notifications" label="Notifications" active={pathname === "/admin/notifications"} />
            </ul>
          </div>

          <div>
            <p className="px-2 text-[10px] font-bold uppercase tracking-[0.16em] text-brand-100/60">Governance</p>
            <ul className="mt-1.5 space-y-0.5">
              <NavLink href="/admin/audit" label="Audit log" active={pathname === "/admin/audit"} />
              <NavLink href="/admin/operations" label="Operations & AI" active={pathname === "/admin/operations"} />
              <NavLink href="/admin/data" label="Data import & verification" active={pathname === "/admin/data"} />
              <NavLink href="/admin/platform" label="Platform settings" active={pathname === "/admin/platform"} />
              <NavLink href="/admin/extraction" label="Read a university website" active={pathname === "/admin/extraction"} />
              <NavLink href="/admin/readiness" label="Go-live & verification" active={pathname === "/admin/readiness"} />
              <NavLink href="/admin/security" label="My security (MFA)" active={pathname === "/admin/security"} />
            </ul>
          </div>

          {grouped.map(({ group, items }) => (
            <div key={group}>
              <p className="px-2 text-[10px] font-bold uppercase tracking-[0.16em] text-brand-100/60">{group}</p>
              <ul className="mt-1.5 space-y-0.5">
                {items.map(([key, meta]) => (
                  <NavLink key={key} href={`/admin/${key}`} label={meta.label} active={pathname === `/admin/${key}`} />
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="mt-6 rounded-xl bg-white/5 p-3 text-[11px]">
          <p className="font-semibold text-white">{user.full_name}</p>
          <p className="text-brand-100/70">{user.email}</p>
          <p className="mt-1 inline-block rounded-full bg-accent-500/20 px-2 py-0.5 font-bold text-accent-400">{user.role}</p>
          <button type="button" onClick={clearSession} className="mt-3 w-full rounded-lg bg-white/10 px-3 py-2 font-semibold text-white hover:bg-white/20">
            Sign out
          </button>
          <Link href="/" className="mt-1.5 block rounded-lg px-3 py-2 text-center font-semibold text-brand-100 hover:bg-white/10">
            View public site ↗
          </Link>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-3 lg:hidden">
          <button type="button" onClick={() => setNavOpen((o) => !o)}
            className="grid h-10 w-10 place-items-center rounded-xl border border-slate-300">{navOpen ? "✕" : "☰"}</button>
          <span className="text-sm font-bold text-slate-900">Scholara Admin</span>
        </header>
        <div className="p-4 sm:p-6">{children}</div>
      </div>
    </div>
  );
}

function NavLink({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <li>
      <Link href={href}
        className={`block rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition ${active ? "bg-accent-500 text-brand-950" : "text-brand-100 hover:bg-white/10 hover:text-white"}`}>
        {label}
      </Link>
    </li>
  );
}

function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"password" | "mfa" | "enrol" | "recovery" | "lost">("password");
  // Set when the account is forced to enrol: a token that may only call the MFA set-up
  // endpoints, plus the secret to show the user and the recovery codes handed back on success.
  const [enrolToken, setEnrolToken] = useState("");
  const [enrolSecret, setEnrolSecret] = useState("");
  const [enrolUri, setEnrolUri] = useState("");
  const [enrolQr, setEnrolQr] = useState("");
  // Step two is finished with this scoped token, never by re-posting the password.
  const [challenge, setChallenge] = useState("");
  const [recovery, setRecovery] = useState<string[]>([]);
  const [pending, setPending] = useState<{ token: string; user: any } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  /** Fetch a fresh secret + QR for the current enrolment token and show the setup screen. */
  const startEnrolment = async (token: string) => {
    const enr = await fetch("/api/v1/admin/auth/mfa/enroll", {
      method: "POST", headers: { Authorization: `Bearer ${token}` },
    });
    const info = await enr.json().catch(() => ({}));
    if (!enr.ok) throw new Error(typeof info?.detail === "string" ? info.detail : "Could not start two-factor set-up.");
    setEnrolToken(token);
    setEnrolSecret(info.manual_entry ?? info.secret ?? "");
    setEnrolUri(info.otpauth_url ?? "");
    setEnrolQr(info.qr_data_uri ?? "");
    setCode("");
    setStage("enrol");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      // Confirming a brand-new secret is a different call, carrying the enrolment token.
      if (stage === "enrol") {
        const res = await fetch("/api/v1/admin/auth/mfa/confirm", {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${enrolToken}` },
          body: JSON.stringify({ code }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "That code was not accepted.");
        // Show the recovery codes before letting the session start - they are never shown again.
        setRecovery(data.recovery_codes ?? []);
        setPending({ token: data.access_token, user: data.user });
        setStage("recovery");
        return;
      }

      // Lost authenticator: password plus one unused recovery code, then re-enrol.
      if (stage === "lost") {
        const res = await fetch("/api/v1/admin/auth/mfa/recover", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password, recovery_code: code.trim() }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(typeof data?.detail === "string" ? data.detail
            : "That recovery code was not accepted. Try another one.");
        }
        await startEnrolment(data.access_token as string);
        return;
      }

      if (stage === "mfa") {
        const res = await fetch("/api/v1/admin/auth/mfa/challenge", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ challenge_token: challenge, code: code.trim() }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(typeof data?.detail === "string" ? data.detail : "That code was not accepted.");
        }
        setSession(data.access_token, data.user);
        return;
      }

      const res = await fetch("/api/v1/admin/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Invalid email or password.");

      // Step 1 succeeded but the account has an authenticator enrolled. Only a scoped,
      // short-lived challenge token comes back - no session until the code is verified.
      if (data.mfa_required) {
        setChallenge(data.challenge_token ?? "");
        setCode("");
        setStage("mfa");
        setErr("");
        return;
      }

      // The role requires MFA and this account has never set it up. The token returned here
      // only opens the enrolment endpoints, so it must not be stored as a session - doing so
      // would produce a dashboard that 403s on every panel with no explanation.
      if (data.mfa_enrolment_required) {
        await startEnrolment(data.access_token as string);
        return;
      }

      setSession(data.access_token, data.user);
    } catch (e: any) {
      setErr(e?.message ?? "Sign in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center bg-brand-950 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl bg-white p-7 shadow-2xl">
        <div className="flex items-center gap-2">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-800 text-base font-black text-white">S</span>
          <div>
            <p className="text-base font-black text-brand-900">Scholara Admin</p>
            <p className="text-[11px] uppercase tracking-wider text-slate-500">Staff sign in</p>
          </div>
        </div>

        {stage === "recovery" ? (
          <div className="mt-6">
            <p className="rounded-lg bg-emerald-50 p-3 text-xs text-emerald-900 ring-1 ring-emerald-200">
              Two-factor is now active. Save these recovery codes somewhere safe — each one works
              once, and they are the only way in if you lose your authenticator. They are not
              shown again.
            </p>
            <ul className="mt-3 grid grid-cols-2 gap-1.5 rounded-xl bg-slate-50 p-3 font-mono text-[12px] text-slate-800">
              {recovery.map((c) => <li key={c}>{c}</li>)}
            </ul>
          </div>
        ) : stage === "enrol" ? (
          <div className="mt-6">
            <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900 ring-1 ring-amber-200">
              {enrolQr
                ? "Two-factor authentication is required for this role. Scan the code below with Google Authenticator, Microsoft Authenticator, Authy or 1Password, then enter the 6-digit code it shows."
                : "Two-factor authentication is required for this role. Add the setup key below to your authenticator app, then enter the 6-digit code it shows."}
            </p>
            <div className="mt-3 rounded-xl bg-slate-50 p-3">
              {enrolQr && (
                <div className="mb-3 flex justify-center rounded-lg bg-white p-2">
                  {/* A data-URI SVG: an <img> cannot execute it, unlike injected markup. */}
                  <img src={enrolQr} alt="Two-factor setup QR code" width={180} height={180} />
                </div>
              )}
              <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Setup key</p>
              <p className="mt-1 break-all font-mono text-[13px] font-semibold text-slate-900">{enrolSecret}</p>
              {enrolUri && (
                <a href={enrolUri} className="mt-2 inline-block text-[11px] font-semibold text-brand-800 underline">
                  Open in your authenticator app
                </a>
              )}
            </div>
            <label className="mt-3 block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Code from the app</span>
              <input value={code} onChange={(e) => setCode(e.target.value)} required autoFocus autoComplete="one-time-code"
                placeholder="123456"
                className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-center font-mono text-lg tracking-[0.25em] outline-none focus:border-brand-600" />
            </label>
          </div>
        ) : stage === "password" ? (
          <>
            <label className="mt-6 block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Email</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username"
                className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm outline-none focus:border-brand-600" />
            </label>
            <label className="mt-3 block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password"
                className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm outline-none focus:border-brand-600" />
            </label>
          </>
        ) : stage === "lost" ? (
          <div className="mt-6">
            <p className="rounded-lg bg-brand-50 p-3 text-xs text-brand-900">
              Enter one of the recovery codes you saved when you set up two-factor for{" "}
              <strong>{email}</strong>. The code is used up immediately and you will register a
              new authenticator straight after.
            </p>
            <label className="mt-3 block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Recovery code</span>
              <input value={code} onChange={(e) => setCode(e.target.value)} required autoFocus
                placeholder="xxxx-xxxx-xxxx"
                className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-center font-mono text-sm tracking-wider outline-none focus:border-brand-600" />
            </label>
            <button type="button"
              onClick={() => { setStage("mfa"); setCode(""); setErr(""); }}
              className="mt-2 text-[11px] font-semibold text-slate-500 underline">
              I still have my authenticator
            </button>
          </div>
        ) : (
          <div className="mt-6">
            <p className="rounded-lg bg-brand-50 p-3 text-xs text-brand-900">
              Two-factor is enabled for <strong>{email}</strong>. Open your authenticator app and
              enter the current 6-digit code, or one of your recovery codes.
            </p>
            <label className="mt-3 block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Authentication code</span>
              <input value={code} onChange={(e) => setCode(e.target.value)} required autoFocus
                autoComplete="one-time-code" placeholder="123456"
                className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-center font-mono text-lg tracking-[0.25em] outline-none focus:border-brand-600" />
            </label>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              <button type="button"
                onClick={() => { setStage("lost"); setCode(""); setErr(""); }}
                className="text-[11px] font-semibold text-brand-800 underline">
                Lost your authenticator?
              </button>
              <button type="button"
                onClick={() => { setStage("password"); setCode(""); setChallenge(""); setErr(""); }}
                className="text-[11px] font-semibold text-slate-500 underline">
                Use a different account
              </button>
            </div>
          </div>
        )}

        {err && <p className="mt-3 rounded-lg bg-rose-50 p-3 text-xs text-rose-800 ring-1 ring-rose-200">{err}</p>}

        {stage === "recovery" ? (
          <button type="button" onClick={() => pending && setSession(pending.token, pending.user)}
            className="mt-5 w-full rounded-xl bg-brand-800 px-4 py-3 text-sm font-bold text-white">
            I have saved them — continue
          </button>
        ) : (
          <button type="submit" disabled={busy}
            className="mt-5 w-full rounded-xl bg-brand-800 px-4 py-3 text-sm font-bold text-white disabled:opacity-60">
            {busy
              ? (stage === "password" ? "Signing in…" : "Verifying…")
              : stage === "mfa" ? "Verify and continue"
              : stage === "lost" ? "Use this recovery code"
              : stage === "enrol" ? "Activate two-factor"
              : "Sign in"}
          </button>
        )}

        <DevLoginHint />
      </form>
    </div>
  );
}

/**
 * Development-only sign-in hint.
 *
 * This used to be unconditional text on the sign-in screen: it printed the seeded
 * administrator's email and password and pointed the reader at a repository helper script that
 * minted a second factor from a published secret. Both belong to a throwaway local database, and
 * a deployed site must show neither. `process.env.NODE_ENV` is inlined at build time, so in a
 * production build this branch becomes `false` and the text is not in the bundle at all - it is
 * not hidden with CSS, and no environment variable can switch it back on.
 */
function DevLoginHint() {
  if (process.env.NODE_ENV !== "development") return null;
  return (
    <p className="mt-4 rounded-lg border border-dashed border-amber-300 bg-amber-50 p-3 text-[11px] leading-relaxed text-amber-900">
      <strong>Local development only.</strong> The development seed creates{" "}
      <code className="rounded bg-amber-200/60 px-1">admin@scholara.com.bd</code> with a published
      password and TOTP secret, both listed at the top of{" "}
      <code className="rounded bg-amber-200/60 px-1">backend/app/seed_data.py</code>. Add the secret
      to any authenticator app to sign in. Never use these credentials on a deployed site.
    </p>
  );
}
