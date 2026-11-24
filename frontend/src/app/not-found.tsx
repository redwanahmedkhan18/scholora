import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-4 py-24 text-center sm:px-6">
      <p className="text-6xl font-black text-brand-800">404</p>
      <h1 className="mt-4 text-2xl font-bold text-slate-900">We do not have that page</h1>
      <p className="mt-3 text-sm leading-relaxed text-slate-600">
        The link may be out of date, or the record may not be published yet. We only publish universities,
        programmes and scholarships we hold source-verified data for — so a missing page usually means
        &ldquo;not verified yet&rdquo;, not &ldquo;does not exist&rdquo;.
      </p>
      <div className="mt-7 flex flex-wrap justify-center gap-3">
        <Link href="/" className="rounded-xl bg-brand-800 px-5 py-3 text-sm font-bold text-white">Back to home</Link>
        <Link href="/universities" className="rounded-xl border border-slate-300 px-5 py-3 text-sm font-bold text-slate-700">University search</Link>
        <Link href="/contact" className="rounded-xl border border-slate-300 px-5 py-3 text-sm font-bold text-slate-700">Ask a counselor</Link>
      </div>
    </div>
  );
}
