import type { Metadata } from "next";
import { api, getVocabularies } from "@/lib/api";
import Assessment from "@/components/assessment";
import { PageHeader } from "@/components/ui";

export const revalidate = 300;
type Dict = Record<string, any>;

export const metadata: Metadata = {
  title: "Free profile assessment & eligibility check",
  description:
    "Enter your CGPA, English score, budget and target intake. Get ranked programme matches, per-criterion eligibility verdicts, a full annual cost estimate and the scholarships whose stated criteria you appear to meet.",
  alternates: { canonical: "/profile-assessment" },
};

export default async function AssessmentPage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const program = Array.isArray(sp.program) ? sp.program[0] : sp.program;
  const [countries, vocabularies] = await Promise.all([
    api<Dict[]>("/countries", 300), getVocabularies(),
  ]);
  const degreeLevels = (vocabularies.degree_levels ?? []) as Array<{ value: string; label: string }>;
  // The Select primitive takes plain strings; codes and labels coincide for these lists.
  const vocab = Object.fromEntries(
    ["funding_sources", "education_levels", "cgpa_scales", "english_tests", "budget_currencies"]
      .map((k) => [k, ((vocabularies[k] ?? []) as Array<{ value: string }>).map((o) => o.value)]),
  );

  return (
    <>
      <PageHeader
        eyebrow="Free · no payment · no obligation"
        title="Profile assessment and eligibility check"
        description="Four short steps. We compare your profile against the stored requirements of every programme in our database and tell you exactly which criteria you meet, which you do not, and which the university has not published."
      />
      <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6">
        <Assessment countries={countries} degreeLevels={degreeLevels} vocab={vocab} initialProgram={program} />
      </div>
    </>
  );
}
