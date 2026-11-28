"use client";

import Link from "next/link";
import { useState } from "react";
import { LEVEL_LABEL, money, months } from "@/lib/format";
import { setProfileToken, syncShortlist } from "@/lib/portal";
import { storedUtm, track } from "@/lib/track";

type Dict = Record<string, any>;

const EMPTY = {
  full_name: "", email: "", phone: "", nationality: "Bangladesh", city: "Dhaka",
  highest_qualification: "Bachelor", major: "", institution: "", cgpa: "", cgpa_scale: "4",
  graduation_year: "", study_gap_years: "", backlogs: "", completed_subjects: "",
  english_test: "IELTS", ielts_overall: "", ielts_lowest_band: "", toefl_ibt: "", pte_academic: "",
  duolingo: "", moi_available: false, gre_score: "", gmat_score: "",
  work_experience_years: "", job_title: "", research_experience: "", publications_count: "",
  research_interests: "", budget_per_year: "", budget_currency: "USD", funding_source: "Family",
  preferred_countries: [] as string[], target_degree_level: "MASTER", target_discipline: "",
  target_intake: "", career_goal: "", consent_marketing: false, consent_contact: false,
};

const num = (v: string) => (v === "" || v === null ? null : Number(v));
const list = (v: string) => v.split(",").map((s) => s.trim()).filter(Boolean);

const STEPS = ["About you", "Academics", "English & tests", "Budget & goal", "Your results"];

export default function Assessment({ countries, degreeLevels = [], vocab = {}, initialProgram }:
  { countries: Dict[]; degreeLevels?: Array<{ value: string; label: string }>;
    /** Remaining profile vocabularies, keyed as served by /vocabularies. */
    vocab?: Record<string, string[]>; initialProgram?: string }) {
  const [step, setStep] = useState(0);
  const [f, setF] = useState({ ...EMPTY });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Dict | null>(null);
  const [eligibility, setEligibility] = useState<Dict | null>(null);
  const [scholarships, setScholarships] = useState<Dict[] | null>(null);
  const [brochure, setBrochure] = useState<Dict | null>(null);
  const [leadSent, setLeadSent] = useState(false);
  const [portalToken, setPortalToken] = useState<string | null>(null);

  const set = (k: string, v: any) => setF((s) => ({ ...s, [k]: v }));

  const profile = () => ({
    full_name: f.full_name, email: f.email || null, phone: f.phone || null,
    nationality: f.nationality, city: f.city || null,
    highest_qualification: f.highest_qualification, major: f.major || null, institution: f.institution || null,
    cgpa: num(f.cgpa), cgpa_scale: num(f.cgpa_scale) ?? 4, graduation_year: num(f.graduation_year),
    study_gap_years: num(f.study_gap_years), backlogs: num(f.backlogs),
    completed_subjects: list(f.completed_subjects),
    english_test: f.english_test, ielts_overall: num(f.ielts_overall), ielts_lowest_band: num(f.ielts_lowest_band),
    toefl_ibt: num(f.toefl_ibt), pte_academic: num(f.pte_academic), duolingo: num(f.duolingo),
    moi_available: f.moi_available, gre_score: f.gre_score || null, gmat_score: f.gmat_score || null,
    work_experience_years: num(f.work_experience_years), job_title: f.job_title || null,
    research_experience: f.research_experience || null, publications_count: num(f.publications_count),
    research_interests: list(f.research_interests),
    budget_per_year: num(f.budget_per_year), budget_currency: f.budget_currency,
    funding_source: f.funding_source || null,
    preferred_countries: f.preferred_countries, preferred_universities: [],
    target_degree_level: f.target_degree_level, target_discipline: f.target_discipline || null,
    target_intake: f.target_intake || null, career_goal: f.career_goal || null,
    consent_marketing: f.consent_marketing, source: "WEBSITE_ASSESSMENT",
  });

  const post = async (path: string, body: Dict) => {
    const res = await fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${path} -> ${res.status}`);
    return res.json();
  };

  const run = async () => {
    if (!f.full_name.trim()) { setError("Please enter your name so the report can be addressed to you."); setStep(0); return; }
    setLoading(true); setError("");
    track("assessment_submit", { level: f.target_degree_level, countries: f.preferred_countries }, "Lead");
    try {
      const p = profile();
      const [match, schol] = await Promise.all([
        post("/api/v1/match/programs", { profile: p, limit: 12 }),
        post("/api/v1/match/scholarships", { profile: p, limit: 10 }).catch(() => null),
      ]);
      setResult(match);
      setScholarships(Array.isArray(schol) ? schol : schol?.results ?? null);

      // If the visitor arrived from a programme page (?program=slug) assess THAT programme,
      // otherwise fall back to the strongest match.
      const focusSlug = initialProgram || match?.results?.[0]?.program?.slug;
      const best = focusSlug ? { program: { slug: focusSlug } } : null;
      if (best) {
        const [elig, broch] = await Promise.all([
          post("/api/v1/eligibility/check", { profile: p, program_slug: best.program.slug }).catch(() => null),
          post("/api/v1/brochures/generate", {
            profile: p, program_slug: best.program.slug, target_intake: f.target_intake || null, channel: "WEB",
          }).catch(() => null),
        ]);
        setEligibility(elig);
        setBrochure(broch);
      }
      // Persist the profile so the student keeps a portal they can return to.
      post("/api/v1/profiles", p)
        .then(async (saved: Dict) => {
          if (!saved?.token) return;
          setPortalToken(saved.token);
          setProfileToken(saved.token, f.full_name);
          await syncShortlist(saved.token);
        })
        .catch(() => {});

      setStep(4);
      if (f.phone.trim() && f.consent_contact) {
        post("/api/v1/leads", {
          full_name: f.full_name, phone: f.phone, email: f.email || null,
          interest_country: f.preferred_countries[0] ?? null, interest_degree: f.target_degree_level,
          interest_subject: f.target_discipline || null, intake: f.target_intake || null,
          source: "WEBSITE", channel_detail: "profile-assessment", utm: storedUtm(),
          page_path: "/profile-assessment", consent_contact: f.consent_contact,
          message: "Submitted the online profile assessment.",
        }).then(() => setLeadSent(true)).catch(() => {});
      }
    } catch {
      setError("We could not reach the matching service. Please try again in a moment, or send us your details on WhatsApp.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
      <div className="card p-6">
        {initialProgram && (
          <p className="mb-5 rounded-xl border border-brand-200 bg-brand-50 p-3 text-xs leading-relaxed text-brand-900">
            Your eligibility report and brochure will be produced for{" "}
            <strong>{initialProgram.replace(/-/g, " ")}</strong>. You will still see every other programme that matches
            your profile below it.
          </p>
        )}
        <ol className="mb-6 flex flex-wrap gap-2">
          {STEPS.map((s, i) => (
            <li key={s}>
              <button type="button" onClick={() => i < 4 && setStep(i)}
                className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${i === step ? "bg-brand-800 text-white" : i < step ? "bg-brand-50 text-brand-800" : "bg-slate-100 text-slate-500"}`}>
                {i + 1}. {s}
              </button>
            </li>
          ))}
        </ol>

        {error && <p className="mb-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-800 ring-1 ring-rose-200">{error}</p>}

        {step === 0 && (
          <Fieldset title="About you" hint="We use this to address your report and, if you ask us to, to call you back.">
            <Row>
              <Text label="Full name *" value={f.full_name} onChange={(v) => set("full_name", v)} placeholder="As on your passport" />
              <Text label="Mobile (WhatsApp)" value={f.phone} onChange={(v) => set("phone", v)} placeholder="+8801XXXXXXXXX" />
            </Row>
            <Row>
              <Text label="Email" value={f.email} onChange={(v) => set("email", v)} type="email" />
              <Text label="City" value={f.city} onChange={(v) => set("city", v)} />
            </Row>
            <Check label="You may contact me about my results on WhatsApp, phone or email."
              checked={f.consent_contact} onChange={(v) => set("consent_contact", v)} />
            <Check label="Send me intake deadlines and scholarship alerts."
              checked={f.consent_marketing} onChange={(v) => set("consent_marketing", v)} />
          </Fieldset>
        )}

        {step === 1 && (
          <Fieldset title="Academic background" hint="CGPA and subject background drive the academic eligibility check.">
            <Row>
              <Select label="Highest qualification" value={f.highest_qualification} onChange={(v) => set("highest_qualification", v)}
                options={vocab.education_levels ?? []} />
              <Text label="Major / subject" value={f.major} onChange={(v) => set("major", v)} placeholder="Computer Science and Engineering" />
            </Row>
            <Row>
              <Text label="Institution" value={f.institution} onChange={(v) => set("institution", v)} placeholder="University name" />
              <Text label="Graduation year" value={f.graduation_year} onChange={(v) => set("graduation_year", v)} type="number" placeholder="2025" />
            </Row>
            <Row>
              <Text label="CGPA" value={f.cgpa} onChange={(v) => set("cgpa", v)} type="number" placeholder="3.60" />
              <Select label="CGPA scale" value={f.cgpa_scale} onChange={(v) => set("cgpa_scale", v)} options={vocab.cgpa_scales ?? []} />
            </Row>
            <Row>
              <Text label="Study gap (years)" value={f.study_gap_years} onChange={(v) => set("study_gap_years", v)} type="number" placeholder="0" />
              <Text label="Backlogs / retakes" value={f.backlogs} onChange={(v) => set("backlogs", v)} type="number" placeholder="0" />
            </Row>
            <Text label="Key subjects completed (comma separated)" value={f.completed_subjects}
              onChange={(v) => set("completed_subjects", v)} placeholder="Linear Algebra, Programming, Statistics" />
          </Fieldset>
        )}

        {step === 2 && (
          <Fieldset title="English and standardised tests" hint="Leave blank if you have not taken a test yet — we will tell you what score each programme needs.">
            <Row>
              <Select label="English test" value={f.english_test} onChange={(v) => set("english_test", v)}
                options={vocab.english_tests ?? []} />
              <Text label="IELTS overall" value={f.ielts_overall} onChange={(v) => set("ielts_overall", v)} type="number" placeholder="6.5" />
            </Row>
            <Row>
              <Text label="Lowest IELTS band" value={f.ielts_lowest_band} onChange={(v) => set("ielts_lowest_band", v)} type="number" placeholder="6.0" />
              <Text label="TOEFL iBT" value={f.toefl_ibt} onChange={(v) => set("toefl_ibt", v)} type="number" />
            </Row>
            <Row>
              <Text label="PTE Academic" value={f.pte_academic} onChange={(v) => set("pte_academic", v)} type="number" />
              <Text label="Duolingo" value={f.duolingo} onChange={(v) => set("duolingo", v)} type="number" />
            </Row>
            <Check label="I can provide a Medium of Instruction (MOI) letter." checked={f.moi_available} onChange={(v) => set("moi_available", v)} />
            <Row>
              <Text label="GRE score" value={f.gre_score} onChange={(v) => set("gre_score", v)} placeholder="e.g. 315" />
              <Text label="GMAT score" value={f.gmat_score} onChange={(v) => set("gmat_score", v)} placeholder="e.g. 600" />
            </Row>
            <Row>
              <Text label="Work experience (years)" value={f.work_experience_years} onChange={(v) => set("work_experience_years", v)} type="number" />
              <Text label="Current job title" value={f.job_title} onChange={(v) => set("job_title", v)} />
            </Row>
          </Fieldset>
        )}

        {step === 3 && (
          <Fieldset title="Budget, destination and goal" hint="Your real budget drives the cost comparison. We would rather show you five affordable options than fifty you cannot fund.">
            <Row>
              <Text label="Budget per year" value={f.budget_per_year} onChange={(v) => set("budget_per_year", v)} type="number" placeholder="25000" />
              <Select label="Currency" value={f.budget_currency} onChange={(v) => set("budget_currency", v)}
                options={vocab.budget_currencies ?? []} />
            </Row>
            <Row>
              <Select label="Funding source" value={f.funding_source} onChange={(v) => set("funding_source", v)}
                options={vocab.funding_sources ?? []} />
              <Select label="Target degree" value={f.target_degree_level} onChange={(v) => set("target_degree_level", v)}
                options={degreeLevels.map((d) => d.value)}
                labels={Object.fromEntries(degreeLevels.map((d) => [d.value, d.label]))} />
            </Row>
            <Row>
              <Text label="Target subject" value={f.target_discipline} onChange={(v) => set("target_discipline", v)} placeholder="Artificial Intelligence" />
              <Text label="Target intake" value={f.target_intake} onChange={(v) => set("target_intake", v)} placeholder="Fall 2027" />
            </Row>
            {f.target_degree_level === "PHD" && (
              <>
                <Text label="Research interests (comma separated)" value={f.research_interests}
                  onChange={(v) => set("research_interests", v)} placeholder="clinical nlp, federated learning" />
                <Text label="Research experience" value={f.research_experience} onChange={(v) => set("research_experience", v)}
                  placeholder="Thesis, lab work, publications" />
              </>
            )}
            <div>
              <span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Preferred destinations</span>
              <div className="flex flex-wrap gap-1.5">
                {countries.filter((c) => c.status !== "COMING_SOON").map((c) => {
                  const on = f.preferred_countries.includes(c.name);
                  return (
                    <button key={c.id} type="button"
                      onClick={() => set("preferred_countries", on ? f.preferred_countries.filter((x) => x !== c.name) : [...f.preferred_countries, c.name])}
                      className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${on ? "bg-brand-800 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}>
                      {c.flag_emoji} {c.name}
                    </button>
                  );
                })}
              </div>
              <p className="mt-1.5 text-[11px] text-slate-500">Leave empty to search every destination.</p>
            </div>
            <Text label="Career goal" value={f.career_goal} onChange={(v) => set("career_goal", v)} placeholder="AI research engineer" />
          </Fieldset>
        )}

        {step === 4 && (
          <Results result={result} eligibility={eligibility} scholarships={scholarships} brochure={brochure} leadSent={leadSent} portalToken={portalToken} />
        )}

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-5">
          <button type="button" disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))}
            className="rounded-xl border border-slate-300 px-5 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-40">
            Back
          </button>
          {step < 3 && (
            <button type="button" onClick={() => setStep((s) => s + 1)}
              className="rounded-xl bg-brand-800 px-6 py-2.5 text-sm font-bold text-white">Continue</button>
          )}
          {step === 3 && (
            <button type="button" onClick={run} disabled={loading}
              className="rounded-xl bg-accent-500 px-6 py-2.5 text-sm font-bold text-brand-950 disabled:opacity-60">
              {loading ? "Checking your profile…" : "Show my results"}
            </button>
          )}
          {step === 4 && (
            <button type="button" onClick={() => setStep(3)} className="rounded-xl bg-brand-800 px-6 py-2.5 text-sm font-bold text-white">
              Change my answers
            </button>
          )}
        </div>
      </div>

      <aside className="space-y-4">
        <div className="card p-5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">What you get</h2>
          <ul className="mt-3 space-y-2 text-sm text-slate-600">
            <li className="flex gap-2"><span className="text-brand-700" aria-hidden>✓</span>Ranked programme matches with the reason for each score</li>
            <li className="flex gap-2"><span className="text-brand-700" aria-hidden>✓</span>Per-criterion eligibility, not a made-up percentage</li>
            <li className="flex gap-2"><span className="text-brand-700" aria-hidden>✓</span>Full annual cost against your stated budget</li>
            <li className="flex gap-2"><span className="text-brand-700" aria-hidden>✓</span>Scholarships whose stated criteria you appear to meet</li>
            <li className="flex gap-2"><span className="text-brand-700" aria-hidden>✓</span>A shareable brochure for your counseling session</li>
          </ul>
        </div>
        <div className="card bg-brand-950 p-5 text-white">
          <h2 className="text-sm font-bold uppercase tracking-wide">Honest by design</h2>
          <p className="mt-2 text-sm leading-relaxed text-brand-100">
            Verdicts are limited to meets, partially meets, does not meet, requires review and information
            unavailable. If a university has not published a requirement, we say so instead of guessing.
          </p>
        </div>
        <div className="card p-5">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-700">Prefer to talk first?</h2>
          <p className="mt-2 text-sm text-slate-600">Book a free session at our Uttara office or ask on WhatsApp.</p>
          <Link href="/appointments" className="mt-3 block rounded-xl bg-brand-800 px-4 py-2.5 text-center text-sm font-bold text-white">Book a counselor</Link>
        </div>
      </aside>
    </div>
  );
}

/* --------------------------------------------------------------- results */
function Results({ result, eligibility, scholarships, brochure, leadSent, portalToken }: {
  result: Dict | null; eligibility: Dict | null; scholarships: Dict[] | null; brochure: Dict | null;
  leadSent: boolean; portalToken?: string | null;
}) {
  if (!result) return <p className="text-sm text-slate-600">Complete the previous steps and press &ldquo;Show my results&rdquo;.</p>;
  const results: Dict[] = result.results ?? [];
  const stats = result.stats ?? {};

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-bold text-slate-900">Your matches</h2>
        <p className="mt-1 text-sm text-slate-600">
          {stats.total} programme{stats.total === 1 ? "" : "s"} assessed · {stats.academic_ok} meet the academic bar ·{" "}
          {stats.within_budget} sit inside your budget · {stats.intake_available} have your intake open.
        </p>
        {leadSent && (
          <p className="mt-2 rounded-lg bg-emerald-50 p-3 text-xs text-emerald-800 ring-1 ring-emerald-200">
            Your details reached our counseling team. Someone from the Uttara office will follow up on WhatsApp.
          </p>
        )}
      </div>

      {portalToken && (
        <div className="rounded-xl border border-brand-200 bg-brand-50 p-4">
          <p className="text-sm font-bold text-brand-900">Your student portal is ready</p>
          <p className="mt-1 text-xs leading-relaxed text-brand-900/80">
            Track applications, upload documents and keep your shortlist in one place. Bookmark this
            link — it is your private key, so do not share it.
          </p>
          <Link href={`/my/${portalToken}`}
            className="mt-3 inline-block rounded-xl bg-brand-800 px-4 py-2.5 text-sm font-bold text-white">
            Open my portal →
          </Link>
        </div>
      )}

      {brochure?.url && (
        <div className="rounded-xl border border-accent-500/40 bg-accent-500/10 p-4">
          <p className="text-sm font-bold text-slate-900">Your personalised brochure is ready</p>
          <p className="mt-1 text-xs text-slate-600">
            A printable {LEVEL_LABEL[brochure.degree_level] ?? brochure.degree_level} report with requirements, costs,
            deadlines and sources — bring it to your counseling session.
          </p>
          <Link href={brochure.url} target="_blank"
            className="mt-3 inline-block rounded-xl bg-brand-800 px-5 py-2.5 text-sm font-bold text-white">
            Open brochure ↗
          </Link>
        </div>
      )}

      {eligibility?.eligibility && (
        <div className="rounded-xl border border-slate-200 p-4">
          <h3 className="text-sm font-bold text-slate-900">
            Eligibility detail — {eligibility.program?.name}
          </h3>
          <p className="mt-1 text-xs text-slate-600">{eligibility.eligibility.summary}</p>
          <ul className="mt-3 space-y-1.5">
            {(eligibility.eligibility.checks ?? []).map((c: Dict) => (
              <li key={c.criterion} className="flex flex-wrap items-center gap-2 text-xs">
                <Verdict status={c.status} />
                <span className="font-semibold text-slate-800">{c.criterion}:</span>
                <span className="text-slate-600">required {c.required} · you {c.provided}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="space-y-3">
        {results.map((r) => (
          <li key={r.program.id} className="rounded-xl border border-slate-200 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <Link href={`/programs/${r.program.slug}`} className="text-base font-bold text-brand-800">{r.program.name}</Link>
                <p className="text-xs text-slate-600">
                  {r.program.university?.country?.flag_emoji} {r.program.university?.name} ·{" "}
                  {LEVEL_LABEL[r.program.degree_level] ?? r.program.degree_level} · {months(r.program.duration_months)}
                </p>
              </div>
              <div className="text-right">
                <p className="text-lg font-black text-slate-900">{Math.round(r.score)}<span className="text-xs font-semibold text-slate-400">/100</span></p>
                <p className="text-[10px] uppercase tracking-wide text-slate-500">match score</p>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              <Verdict status={r.eligibility?.overall ?? "INFORMATION_UNAVAILABLE"} />
              <span className="chip bg-slate-100 text-slate-700">{money(r.cost?.estimated_annual_total, r.cost?.currency)} / year all-in</span>
              {r.cost?.budget_status && (
                <span className={`chip ring-1 ${r.cost.budget_status === "WITHIN_STATED_BUDGET" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : r.cost.budget_status === "SLIGHTLY_ABOVE_BUDGET" ? "bg-amber-50 text-amber-800 ring-amber-200" : "bg-rose-50 text-rose-700 ring-rose-200"}`}>
                  {String(r.cost.budget_status).replace(/_/g, " ").toLowerCase()}
                </span>
              )}
              {r.intake?.label && <span className="chip bg-brand-50 text-brand-800">{r.intake.label} · deadline {r.intake.application_deadline}</span>}
            </div>

            <ul className="mt-3 space-y-1">
              {(r.reasons ?? []).map((x: Dict, i: number) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-[11px]">
                  <Verdict status={x.status} small />
                  <span className="font-semibold text-slate-700">{x.factor}:</span>
                  <span className="text-slate-600">{x.detail}</span>
                </li>
              ))}
            </ul>

            {(r.scholarships ?? []).length > 0 && (
              <p className="mt-2 text-[11px] text-slate-600">
                Funding listed: {r.scholarships.map((s: Dict) => s.name).join(" · ")}
              </p>
            )}
          </li>
        ))}
      </ul>

      {scholarships && scholarships.length > 0 && (
        <div>
          <h3 className="text-sm font-bold text-slate-900">Scholarships whose stated criteria you appear to meet</h3>
          <ul className="mt-2 space-y-2">
            {scholarships.slice(0, 6).map((s: Dict, i: number) => {
              const sc = s.scholarship ?? s;
              return (
                <li key={sc.id ?? i} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 p-3">
                  <div>
                    <Link href={`/scholarships/${sc.slug}`} className="text-sm font-semibold text-brand-800">{sc.name}</Link>
                    <p className="text-[11px] text-slate-500">{sc.provider}</p>
                  </div>
                  {s.status && <Verdict status={s.status} />}
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-[11px] text-slate-500">
            &ldquo;Appear to be met&rdquo; refers to the published criteria only. Scholarships remain competitive and
            discretionary.
          </p>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- form bits */
function Verdict({ status, small }: { status: string; small?: boolean }) {
  const map: Dict = {
    MEETS: ["Meets", "bg-emerald-50 text-emerald-800 ring-emerald-200"],
    PARTIALLY_MEETS: ["Partially meets", "bg-amber-50 text-amber-800 ring-amber-200"],
    DOES_NOT_MEET: ["Does not meet", "bg-rose-50 text-rose-800 ring-rose-200"],
    REQUIRES_REVIEW: ["Requires review", "bg-sky-50 text-sky-800 ring-sky-200"],
    INFORMATION_UNAVAILABLE: ["Information unavailable", "bg-slate-100 text-slate-700 ring-slate-200"],
  };
  const [label, cls] = map[status] ?? map.INFORMATION_UNAVAILABLE;
  return <span className={`chip ring-1 ${cls} ${small ? "text-[10px]" : ""}`}>{label}</span>;
}

function Fieldset({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <fieldset className="space-y-4">
      <legend className="text-lg font-bold text-slate-900">{title}</legend>
      {hint && <p className="text-sm text-slate-500">{hint}</p>}
      {children}
    </fieldset>
  );
}
function Row({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2">{children}</div>;
}
function Text({ label, value, onChange, placeholder, type = "text" }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        step={type === "number" ? "any" : undefined}
        className="w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm outline-none focus:border-brand-600" />
    </label>
  );
}
function Select({ label, value, onChange, options, labels }: {
  label: string; value: string; onChange: (v: string) => void; options: string[]; labels?: Record<string, string>;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-brand-600">
        {options.map((o) => <option key={o} value={o}>{labels?.[o] ?? o}</option>)}
      </select>
    </label>
  );
}
function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-2.5 text-sm text-slate-700">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-brand-700" />
      <span>{label}</span>
    </label>
  );
}
