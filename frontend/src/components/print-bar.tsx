"use client";

import { useEffect, useState } from "react";
import { track } from "@/lib/track";

export default function PrintBar() {
  const [copied, setCopied] = useState(false);
  useEffect(() => { track("brochure_view", { path: window.location.pathname }); }, []);

  return (
    <div className="no-print mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-brand-50 p-4">
      <p className="text-xs leading-relaxed text-brand-900">
        This is your personal copy — the link is private, so share it only with people you trust.
        Save it as a PDF to attach to an application or bring it to a counseling session.
      </p>
      <div className="flex gap-2">
        <button type="button"
          onClick={() => { track("brochure_print"); window.print(); }}
          className="rounded-xl bg-brand-800 px-4 py-2 text-sm font-bold text-white">
          Print / save as PDF
        </button>
        <button type="button"
          onClick={() => {
            navigator.clipboard?.writeText(window.location.href);
            setCopied(true); setTimeout(() => setCopied(false), 1800);
            track("brochure_copy_link");
          }}
          className="rounded-xl border border-brand-300 px-4 py-2 text-sm font-bold text-brand-800">
          {copied ? "Link copied" : "Copy link"}
        </button>
      </div>
    </div>
  );
}
