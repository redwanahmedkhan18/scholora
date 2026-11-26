import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { apiOrNull, getSettings } from "@/lib/api";
import { contactFromSettings } from "@/lib/contact";
import { LEVEL_LABEL, dateLabel, money, months, sourceTypeLabel } from "@/lib/format";
import PrintBar from "@/components/print-bar";

export const dynamic = "force-dynamic";
type Dict = Record<string, any>;
type Props = { params: Promise<{ token: string }> };

export const metadata: Metadata = {
  title: "Your personalised brochure",
  robots: { index: false, follow: false, nocache: true },
};

const yes = (v: any) => (v === true ? "Yes" : v === false ? "No" : v ?? "—");

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="mt-6 break-inside-avoid rounded-xl border border-slate-200 p-5">
      <h2 className="text-sm font-black uppercase tracking-[0.1em] text-brand-900">{title}</h2>
      {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function KV({ rows }: { rows: [string, any][] }) {
  const clean = rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0));
  if (clean.length === 0) return <p className="text-sm text-slate-500">Not published by the source.</p>;
  return (
    <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
      {clean.map(([k, v]) => (
        <div key={k} className="flex gap-3 text-sm">
          <dt className="w-44 shrink-0 text-slate-500">{k}</dt>
          <dd className="font-medium text-slate-900">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Provenance({ o }: { o: Dict }) {
  if (!o?.source_url && !o?.verified_at) return null;
  return (
    <p className="mt-3 border-t border-dashed border-slate-200 pt-2 text-[10px] leading-relaxed text-slate-500">
      Source: {o.source_url ? <a href={o.source_url} className="text-brand-700 underline underline-offset-2">{o.source_url}</a> : "not recorded"}
      {o.source_type ? ` · ${sourceTypeLabel(o.source_type)}` : ""}
      {o.verified_at ? ` · verified ${dateLabel(o.verified_at)}` : ""}
      {o.confidence ? ` · status ${String(o.confidence).replace(/_/g, " ").toLowerCase()}` : ""}
    </p>
  );
}

export default async function BrochurePage({ params }: Props) {
  const { token } = await params;
  const [doc, settings] = await Promise.all([apiOrNull<Dict>(`/brochures/${token}`, 0), getSettings()]);
  if (!doc) notFound();

  const b: Dict = doc.payload ?? {};
  const meta = b.meta ?? {};
  const cover = b.cover ?? {};
  const uni = b.university ?? {};
  const prog = b.program ?? {};
  const req = b.requirements ?? {};
  const costs = b.costs ?? {};
  const level = (doc.degree_level ?? meta.degree_level ?? "").toUpperCase();

  return (
    <div className="mx-auto max-w-4xl bg-white px-5 py-8 print:max-w-none print:px-0 sm:px-8">
      <PrintBar />

      {/* ---------------------------------------------------------- cover */}
      <header className="break-inside-avoid border-b-4 border-accent-500 pb-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-2xl font-black tracking-tight text-brand-900">Scholara</p>
            <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Global education intelligence</p>
          </div>
          <div className="text-right text-[11px] text-slate-500">
            <p>Generated {dateLabel(meta.generated_at ?? doc.created_at)}</p>
            <p>Reference {doc.token ?? token} · v{doc.version ?? 1}</p>
          </div>
        </div>

        <h1 className="mt-5 text-2xl font-bold leading-tight text-slate-900">
          {cover.title ?? "Personalised University Guide"}
        </h1>
        <p className="mt-1.5 text-base font-semibold text-brand-800">
          {cover.flag} {cover.program}{cover.award ? ` (${cover.award})` : ""}
        </p>
        <p className="text-sm text-slate-600">{cover.university}{cover.country ? ` · ${cover.country}` : ""}</p>

        <div className="mt-4 flex flex-wrap gap-2 text-[11px]">
          <span className="chip bg-brand-50 text-brand-800">{LEVEL_LABEL[level] ?? level}</span>
          {(doc.student_name ?? meta.prepared_for) && <span className="chip bg-slate-100 text-slate-700">Prepared for {doc.student_name ?? meta.prepared_for}</span>}
          {(doc.target_intake ?? meta.target_intake) && <span className="chip bg-accent-500/15 text-accent-600">Target intake {doc.target_intake ?? meta.target_intake}</span>}
        </div>
      </header>

      {/* ------------------------------------------------------ eligibility */}
      {b.eligibility && (
        <Section title="Your eligibility against this programme"
          subtitle="Each line compares a published requirement with what you told us. Nothing here is a prediction of an admission decision.">
          <p className="rounded-lg bg-slate-50 p-3 text-sm leading-relaxed text-slate-800">{b.eligibility.summary}</p>
          <div className="mt-3 overflow-hidden rounded-lg border border-slate-200">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
                <tr><th className="px-3 py-2">Criterion</th><th className="px-3 py-2">Required</th><th className="px-3 py-2">You provided</th><th className="px-3 py-2">Verdict</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {(b.eligibility.checks ?? []).map((c: Dict, i: number) => (
                  <tr key={i}>
                    <td className="px-3 py-2 font-medium text-slate-900">{c.criterion}</td>
                    <td className="px-3 py-2 text-slate-600">{c.required ?? "—"}</td>
                    <td className="px-3 py-2 text-slate-600">{c.provided ?? "—"}</td>
                    <td className="px-3 py-2 text-xs font-bold uppercase text-slate-900">{String(c.status).replace(/_/g, " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
            Overall: {String(b.eligibility.overall ?? "").replace(/_/g, " ")}
          </p>
        </Section>
      )}

      {/* --------------------------------------------------------- programme */}
      <Section title="Programme detail">
        <KV rows={[
          ["Programme", prog.name], ["Award", prog.award], ["Level", LEVEL_LABEL[prog.degree_level] ?? prog.degree_level],
          ["Duration", months(prog.duration_months)], ["Credits", prog.credits], ["Department", prog.department],
          ["Faculty / school", prog.faculty_school], ["Study mode", prog.study_mode], ["Language", prog.language],
          ["Campus", prog.campus], ["Specialisations", prog.specializations],
        ]} />
        {prog.overview && <p className="mt-3 text-sm leading-relaxed text-slate-700">{prog.overview}</p>}
        {prog.career_outcomes && (
          <p className="mt-2 text-sm leading-relaxed text-slate-700"><strong>Career outcomes:</strong> {prog.career_outcomes}</p>
        )}
        {prog.application_procedure && (
          <p className="mt-2 text-sm leading-relaxed text-slate-700"><strong>How to apply:</strong> {prog.application_procedure}</p>
        )}
        <Provenance o={prog} />
      </Section>

      {/* -------------------------------------------------------- university */}
      <Section title="About the university">
        <KV rows={[
          ["University", uni.name], ["City", uni.city], ["Campus", uni.campus],
          ["Established", uni.established_year], ["Students", uni.students],
          ["International students", uni.international_students], ["Accreditation", uni.accreditation],
          ["Website", uni.website], ["Application portal", uni.application_portal],
        ]} />
        {uni.overview && <p className="mt-3 text-sm leading-relaxed text-slate-700">{uni.overview}</p>}
        {(uni.rankings ?? []).length > 0 && (
          <table className="mt-3 w-full text-left text-sm">
            <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2">Provider</th><th className="px-3 py-2">Edition</th><th className="px-3 py-2">Overall</th><th className="px-3 py-2">National</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {uni.rankings.map((r: Dict, i: number) => (
                <tr key={i}>
                  <td className="px-3 py-2 font-medium text-slate-900">{r.provider}</td>
                  <td className="px-3 py-2 text-slate-600">{r.edition_year}</td>
                  <td className="px-3 py-2 text-slate-600">{r.rank_overall ?? "—"}</td>
                  <td className="px-3 py-2 text-slate-600">{r.rank_national ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Provenance o={uni} />
      </Section>

      {/* ------------------------------------------------------ requirements */}
      <Section title="Entry requirements"
        subtitle={req.available === false ? "The source has not published a complete requirement set for this programme." : undefined}>
        <h3 className="text-xs font-bold uppercase tracking-wide text-slate-600">Academic</h3>
        <div className="mt-2">
          <KV rows={[
            ["Minimum qualification", req.academic?.min_qualification],
            ["Minimum CGPA", req.academic?.min_cgpa ? `${req.academic.min_cgpa} / ${req.academic.cgpa_scale ?? 4}` : null],
            ["Relevant majors", req.academic?.relevant_majors],
            ["Required subjects", req.academic?.required_subjects],
            ["Prerequisites", req.academic?.prerequisites],
            ["Degree equivalency", req.academic?.degree_equivalency],
            ["Study gap policy", req.academic?.study_gap_policy],
            ["Work experience", req.academic?.work_experience_years ? `${req.academic.work_experience_years} year(s)` : null],
          ]} />
        </div>

        <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-slate-600">English language</h3>
        <div className="mt-2">
          <KV rows={[
            ["IELTS overall", req.english?.ielts_overall],
            ["IELTS minimum band", req.english?.ielts_band_min],
            ["TOEFL iBT", req.english?.toefl_ibt], ["PTE Academic", req.english?.pte_academic],
            ["Duolingo", req.english?.duolingo], ["Cambridge", req.english?.cambridge],
            ["MOI accepted", yes(req.english?.moi_accepted)],
            ["Other accepted tests", req.english?.other_accepted_tests],
          ]} />
        </div>

        {req.standardised_tests && (
          <>
            <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-slate-600">Standardised tests</h3>
            <div className="mt-2">
              <KV rows={Object.entries(req.standardised_tests).map(([k, v]) => [k.replace(/_/g, " "), typeof v === "boolean" ? yes(v) : v]) as [string, any][]} />
            </div>
          </>
        )}

        {(req.masters_specific || req.doctoral_specific || req.bachelor_specific) && (
          <>
            <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-slate-600">
              {LEVEL_LABEL[level] ?? level}-specific requirements
            </h3>
            <div className="mt-2">
              <KV rows={Object.entries(req.masters_specific ?? req.doctoral_specific ?? req.bachelor_specific ?? {})
                .map(([k, v]) => [k.replace(/_/g, " "), Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? yes(v) : v]) as [string, any][]} />
            </div>
          </>
        )}

        {(req.documents ?? []).length > 0 && (
          <>
            <h3 className="mt-4 text-xs font-bold uppercase tracking-wide text-slate-600">Document checklist</h3>
            <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
              {req.documents.map((d: string) => <li key={d} className="text-sm text-slate-700">▢ {d}</li>)}
            </ul>
          </>
        )}

        {req.notes && <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">{req.notes}</p>}
        <Provenance o={req} />
      </Section>

      {/* -------------------------------------------------------------- cost */}
      {(costs.breakdown ?? []).length > 0 && (
        <Section title="What a year actually costs" subtitle="Tuition plus the living and compulsory costs students forget to budget for.">
          <table className="w-full text-left text-sm">
            <tbody className="divide-y divide-slate-100">
              {costs.breakdown.map((x: Dict, i: number) => (
                <tr key={i} className={x.included_in_total ? "" : "text-slate-500"}>
                  <td className="px-3 py-2">{x.label}</td>
                  <td className="px-3 py-2 text-right font-semibold">{money(x.amount, costs.currency)}</td>
                </tr>
              ))}
              <tr className="bg-brand-50">
                <td className="px-3 py-2 font-bold text-brand-900">Estimated annual total</td>
                <td className="px-3 py-2 text-right font-black text-brand-900">{money(costs.estimated_annual_total, costs.currency)}</td>
              </tr>
              {costs.one_time_costs ? (
                <tr>
                  <td className="px-3 py-2 text-slate-600">One-time costs (first year only)</td>
                  <td className="px-3 py-2 text-right font-semibold text-slate-700">{money(costs.one_time_costs, costs.currency)}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
          {costs.budget_status && (
            <p className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-800">
              Against your stated budget: <strong>{String(costs.budget_status).replace(/_/g, " ").toLowerCase()}</strong>
              {costs.budget_gap ? ` — a gap of about ${money(costs.budget_gap, costs.budget_currency ?? costs.currency)} per year.` : "."}
            </p>
          )}
          {costs.note && <p className="mt-2 text-[11px] leading-relaxed text-slate-500">{costs.note}</p>}
        </Section>
      )}

      {/* ----------------------------------------------------------- funding */}
      {(b.scholarships ?? []).length > 0 && (
        <Section title="Funding to pursue" subtitle="Listed because your profile appears to meet the stated criteria — not as an award or a guarantee.">
          <ul className="space-y-3">
            {b.scholarships.map((s: Dict, i: number) => (
              <li key={i} className="rounded-lg border border-slate-200 p-3">
                <p className="text-sm font-bold text-slate-900">{s.name}</p>
                <p className="text-xs text-slate-600">{s.provider}</p>
                <p className="mt-1 text-sm text-slate-700">
                  {s.amount_type === "FULL" ? "Full award" : s.amount_type === "PERCENTAGE" ? `${s.percentage}% of tuition` : money(s.amount_value, s.currency)}
                  {s.covers_tuition ? " · covers tuition" : ""}{s.stipend ? ` · ${s.stipend}` : ""}
                </p>
                {s.eligibility_summary && <p className="mt-1 text-xs leading-relaxed text-slate-600">{s.eligibility_summary}</p>}
                <p className="mt-1 text-[11px] text-slate-500">
                  {s.deadline ? `Deadline ${dateLabel(s.deadline)}. ` : ""}{s.application_method}
                </p>
                <Provenance o={s} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* ----------------------------------------------------------- intakes */}
      {(b.intakes ?? []).length > 0 && (
        <Section title="Intakes and deadlines" subtitle="Programme-specific — these do not necessarily match the university's general intake calendar.">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
              <tr><th className="px-3 py-2">Intake</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Opens</th><th className="px-3 py-2">Deadline</th><th className="px-3 py-2">Classes start</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {b.intakes.map((i: Dict, k: number) => (
                <tr key={k}>
                  <td className="px-3 py-2 font-medium text-slate-900">{i.label}</td>
                  <td className="px-3 py-2 text-slate-600">{i.status}</td>
                  <td className="px-3 py-2 text-slate-600">{dateLabel(i.application_opens)}</td>
                  <td className="px-3 py-2 font-semibold text-slate-900">{dateLabel(i.application_deadline)}</td>
                  <td className="px-3 py-2 text-slate-600">{dateLabel(i.class_start)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* --------------------------------------------------------- PhD extra */}
      {b.phd && (
        <Section title="Research and supervision" subtitle="Doctoral applications are supervisor-led: alignment matters more than rank.">
          {(b.phd.research_areas ?? []).length > 0 && (
            <p className="text-sm text-slate-700"><strong>Research areas:</strong> {b.phd.research_areas.join(", ")}</p>
          )}
          {(b.phd.faculty ?? []).length > 0 && (
            <ul className="mt-3 space-y-2">
              {b.phd.faculty.map((f: Dict, i: number) => (
                <li key={i} className="rounded-lg border border-slate-200 p-3 text-sm">
                  <p className="font-bold text-slate-900">{f.full_name} <span className="font-normal text-slate-500">— {f.title}</span></p>
                  {f.research_interests && <p className="text-xs text-slate-600">{(f.research_interests ?? []).join(", ")}</p>}
                  <p className="mt-1 text-[11px] text-slate-500">
                    {f.email ? `${f.email} (${String(f.email_status ?? "").replace(/_/g, " ").toLowerCase()})` : "Email not published on an official source — contact the department instead."}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {b.phd.funding && <p className="mt-3 text-sm leading-relaxed text-slate-700">{b.phd.funding}</p>}
          {b.phd.notes && <p className="mt-2 rounded-lg bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">{b.phd.notes}</p>}
        </Section>
      )}

      {/* ------------------------------------------------------ student life */}
      {b.student_life && (
        <Section title="Life as a student there">
          {Object.entries(b.student_life).filter(([, v]) => v).map(([k, v]) => (
            <p key={k} className="mt-1.5 text-sm leading-relaxed text-slate-700">
              <strong className="capitalize">{k.replace(/_/g, " ")}:</strong> {String(v)}
            </p>
          ))}
        </Section>
      )}

      {/* ----------------------------------------------------------- sources */}
      {(b.sources ?? []).length > 0 && (
        <Section title="Every figure above, and where it came from"
          subtitle="If a source is marked NEEDS REVIEW, treat the figure as indicative and confirm it on the official page before you pay anything.">
          <ol className="space-y-1.5">
            {b.sources.map((s: Dict, i: number) => (
              <li key={i} className="text-[11px] leading-relaxed text-slate-600">
                <strong className="text-slate-800">{i + 1}. {s.label}</strong> — {" "}
                <a href={s.url} className="break-all text-brand-700 underline underline-offset-2">{s.url}</a>
                {s.source_type ? ` · ${sourceTypeLabel(s.source_type)}` : ""}
                {s.verified_at ? ` · verified ${dateLabel(s.verified_at)}` : ""}
                {s.confidence ? ` · ${String(s.confidence).replace(/_/g, " ").toLowerCase()}` : ""}
              </li>
            ))}
          </ol>
        </Section>
      )}

      <footer className="mt-8 break-inside-avoid border-t border-slate-200 pt-5 text-[10px] leading-relaxed text-slate-500">
        <p>{meta.disclaimer}</p>
        <p className="mt-2">
          {/* Imprint is assembled from site settings so a change of office, phone or email
              reaches every previously generated brochure without a redeploy. */}
          {[settings.brand_name, settings.office_address,
            contactFromSettings(settings).email, contactFromSettings(settings).phoneDisplay]
            .filter(Boolean)
            .join(" · ")}
          {". "}
          This document is not an offer of admission, a scholarship award, or a visa guarantee. Universities may change
          requirements, fees and deadlines at any time.
        </p>
      </footer>
    </div>
  );
}
