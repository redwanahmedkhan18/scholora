"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useAssistantConversation } from "@/lib/use-assistant-conversation";
import type { Turn } from "@/lib/assistant-lifecycle";
import { track, whatsappHref } from "@/lib/track";

type Dict = Record<string, any>;

export default function AssistantWidget({ settings }: { settings: Dict }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { turns, input, setInput, busy, send } = useAssistantConversation(pathname);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (pathname.startsWith("/admin") || pathname.startsWith("/brochure")) return null;

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => { setOpen(true); track("assistant_open", { path: pathname }); }}
          aria-label="Ask the Scholara study assistant"
          className="no-print fixed bottom-36 right-4 z-40 flex items-center gap-2 rounded-full bg-brand-800 px-4 py-3 text-sm font-bold text-white shadow-xl shadow-brand-950/25 transition hover:bg-brand-700 md:bottom-24"
        >
          <span aria-hidden className="text-base leading-none">✦</span>
          <span className="hidden sm:inline">Ask Scholara</span>
        </button>
      )}

      {open && (
        <div
          role="dialog"
          aria-label="Scholara study assistant"
          className="no-print fixed inset-x-0 bottom-0 z-50 flex h-[85vh] flex-col rounded-t-2xl bg-white shadow-2xl ring-1 ring-slate-200 sm:inset-x-auto sm:right-4 sm:bottom-4 sm:h-[36rem] sm:w-[26rem] sm:rounded-2xl"
        >
          <header className="flex items-center gap-3 rounded-t-2xl bg-brand-900 px-4 py-3 text-white">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-white/15 text-base" aria-hidden>✦</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold">Scholara assistant</p>
              <p className="truncate text-[11px] text-brand-100">Answers only from source-verified records</p>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label="Close assistant"
              className="grid h-8 w-8 place-items-center rounded-lg bg-white/10 text-sm hover:bg-white/20">✕</button>
          </header>

          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto bg-slate-50 px-3 py-4">
            {turns.map((turn, i) => (
              <Bubble key={i} turn={turn} onPick={send} busy={busy} settings={settings} />
            ))}
          </div>

          <form
            onSubmit={(e) => { e.preventDefault(); void send(input); }}
            className="flex items-center gap-2 border-t border-slate-200 bg-white px-3 py-3"
          >
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about a university, cost, intake or scholarship…"
              aria-label="Your question"
              className="min-w-0 flex-1 rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm outline-none focus:border-brand-600"
            />
            <button type="submit" disabled={busy || !input.trim()}
              className="rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50">
              {busy ? "…" : "Ask"}
            </button>
          </form>

          <p className="border-t border-slate-100 bg-white px-3 pb-3 pt-2 text-[10px] leading-relaxed text-slate-500">
            Not an admission, scholarship or visa decision. Figures come from official sources with a
            verification date — always confirm on the university website before you pay anything.
          </p>
        </div>
      )}
    </>
  );
}

function Bubble({ turn, onPick, busy, settings }:
  { turn: Turn; onPick: (q: string) => void; busy: boolean; settings: Dict }) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand-800 px-3.5 py-2.5 text-sm text-white">{turn.text}</p>
      </div>
    );
  }

  if (turn.pending) {
    return (
      <div className="flex gap-1.5 rounded-2xl rounded-bl-sm bg-white px-3.5 py-3 ring-1 ring-slate-200 w-fit">
        {[0, 1, 2].map((d) => (
          <span key={d} className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400"
            style={{ animationDelay: `${d * 120}ms` }} />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="max-w-[92%] rounded-2xl rounded-bl-sm bg-white px-3.5 py-3 text-sm leading-relaxed text-slate-800 ring-1 ring-slate-200">
        {turn.text.split("\n").filter(Boolean).map((line, i) => (
          <p key={i} className={line.startsWith("-") || line.startsWith("  ") ? "pl-1 text-[13px]" : "mt-1.5 first:mt-0"}>
            {line.replace(/\*\*/g, "")}
          </p>
        ))}

        {turn.grounded === false && (
          <p className="mt-2 rounded-lg bg-amber-50 p-2 text-[11px] text-amber-900 ring-1 ring-amber-200">
            No verified record matched this, so nothing was invented.
          </p>
        )}

        {!!turn.sources?.length && (
          <details className="mt-2.5 rounded-lg bg-slate-50 p-2 ring-1 ring-slate-200">
            <summary className="cursor-pointer text-[11px] font-bold text-slate-700">
              Sources ({turn.sources.length})
            </summary>
            <ul className="mt-1.5 space-y-1.5">
              {turn.sources.map((s, i) => (
                <li key={i} className="text-[11px] leading-snug">
                  {s.page ? (
                    <Link href={s.page} className="font-semibold text-brand-800 underline">{s.label}</Link>
                  ) : (
                    <span className="font-semibold text-slate-700">{s.label}</span>
                  )}
                  {s.url && (
                    <>
                      {" · "}
                      <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="text-slate-500 underline">
                        official source
                      </a>
                    </>
                  )}
                  {s.verified_at && <span className="text-slate-400"> · verified {String(s.verified_at).slice(0, 10)}</span>}
                  {s.confidence && s.confidence !== "VERIFIED" && (
                    <span className="ml-1 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-bold text-amber-900">
                      {String(s.confidence).replace(/_/g, " ")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}

        {turn.escalated && settings?.whatsapp_number && (
          <a href={whatsappHref(settings.whatsapp_number, settings.whatsapp_default_message, "(from the assistant)")}
            target="_blank" rel="noopener noreferrer"
            onClick={() => track("whatsapp_click", { placement: "assistant" }, "Contact")}
            className="mt-2.5 block rounded-lg bg-emerald-600 px-3 py-2 text-center text-xs font-bold text-white hover:bg-emerald-500">
            Continue with a counselor on WhatsApp
          </a>
        )}
      </div>

      {!!turn.suggestions?.length && !busy && (
        <div className="flex flex-wrap gap-1.5">
          {turn.suggestions.slice(0, 4).map((s) => (
            <button key={s} type="button" onClick={() => onPick(s)}
              className="rounded-full bg-white px-3 py-1.5 text-[11px] font-semibold text-brand-800 ring-1 ring-brand-200 hover:bg-brand-50">
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
