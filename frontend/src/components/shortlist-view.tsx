"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LEVEL_LABEL, money } from "@/lib/format";
import { getProfileToken, syncShortlist } from "@/lib/portal";
import { track } from "@/lib/track";

type Dict = Record<string, any>;

function read(): Dict[] {
  try { return JSON.parse(localStorage.getItem("scholara.shortlist") ?? "[]"); } catch { return []; }
}

export default function ShortlistView() {
  const [items, setItems] = useState<Dict[] | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [synced, setSynced] = useState<number | null>(null);

  useEffect(() => {
    setItems(read());
    setToken(getProfileToken());
    const on = () => { setItems(read()); setToken(getProfileToken()); };
    window.addEventListener("scholara:shortlist", on);
    window.addEventListener("scholara:profile", on);
    window.addEventListener("storage", on);
    return () => {
      window.removeEventListener("scholara:shortlist", on);
      window.removeEventListener("scholara:profile", on);
      window.removeEventListener("storage", on);
    };
  }, []);

  const saveToPortal = async () => {
    const added = await syncShortlist(token);
    setSynced(added);
  };

  const remove = (slug: string) => {
    const next = read().filter((x) => x.slug !== slug);
    localStorage.setItem("scholara.shortlist", JSON.stringify(next));
    setItems(next);
    window.dispatchEvent(new Event("scholara:shortlist"));
    track("shortlist_remove", { slug });
  };

  const clear = () => {
    localStorage.setItem("scholara.shortlist", "[]");
    setItems([]);
    window.dispatchEvent(new Event("scholara:shortlist"));
  };

  if (items === null) return <p className="text-sm text-slate-500">Loading your shortlist…</p>;

  if (items.length === 0) {
    return (
      <div className="card p-10 text-center">
        <p className="text-base font-semibold text-slate-800">Your shortlist is empty</p>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
          Press &ldquo;Add to shortlist&rdquo; on any programme, university or scholarship. The list lives in this
          browser only — nothing is sent anywhere until you ask us to review it.
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-3">
          <Link href="/programs" className="rounded-xl bg-brand-800 px-5 py-2.5 text-sm font-bold text-white">Browse programmes</Link>
          <Link href="/universities" className="rounded-xl border border-slate-300 px-5 py-2.5 text-sm font-bold text-slate-700">Browse universities</Link>
        </div>
      </div>
    );
  }

  const programs = items.filter((x) => x.type === "program");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-600"><strong className="text-slate-900">{items.length}</strong> saved item{items.length === 1 ? "" : "s"}</p>
        <div className="flex flex-wrap gap-2">
          {programs.length >= 2 && (
            <Link href={`/compare?slugs=${programs.map((p) => p.slug).join(",")}`}
              className="rounded-xl bg-accent-500 px-4 py-2.5 text-sm font-bold text-brand-950">
              Compare {programs.length} programmes
            </Link>
          )}
          {token ? (
            <button type="button" onClick={saveToPortal}
              className="rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">
              {synced === null ? "Save to my portal" : synced > 0 ? `Saved ${synced} item${synced === 1 ? "" : "s"}` : "Already saved"}
            </button>
          ) : (
            <Link href="/profile-assessment" className="rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">
              Save to a portal
            </Link>
          )}
          <Link href="/appointments" className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-bold text-slate-700">Review with a counselor</Link>
          <button type="button" onClick={clear} className="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-600">Clear all</button>
        </div>
      </div>

      <ul className="space-y-3">
        {items.map((x) => (
          <li key={x.slug} className="card flex flex-wrap items-center justify-between gap-3 p-4">
            <div className="min-w-0">
              <span className="chip bg-slate-100 text-slate-600">{x.type}</span>
              <Link href={x.href ?? "#"} className="mt-1 block text-sm font-bold text-brand-800">{x.name}</Link>
              <p className="text-[11px] text-slate-500">
                {x.meta}
                {x.level ? ` · ${LEVEL_LABEL[x.level] ?? x.level}` : ""}
                {x.tuition ? ` · ${money(x.tuition, x.currency)}/yr` : ""}
              </p>
            </div>
            <button type="button" onClick={() => remove(x.slug)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50">
              Remove
            </button>
          </li>
        ))}
      </ul>

      <p className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs leading-relaxed text-slate-600">
        {token ? (
          <>
            This list lives in your browser. <strong>Save it to your portal</strong> to keep it against
            your student record and reach it from any device —{" "}
            <Link href={`/my/${token}`} className="font-bold text-brand-800 underline">open my portal</Link>.
          </>
        ) : (
          <>
            Your shortlist is stored in this browser only — clearing browser data or switching device will
            empty it. Run the free profile assessment to get a portal, and your saved items move across
            automatically.
          </>
        )}
      </p>
    </div>
  );
}
