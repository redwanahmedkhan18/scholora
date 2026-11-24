import type { Metadata } from "next";
import CompareView from "@/components/compare-view";
import { Disclaimer, PageHeader } from "@/components/ui";

export const metadata: Metadata = {
  title: "Compare programmes",
  description: "Put up to four programmes side by side: fees, all-in annual cost, CGPA and IELTS requirements, tests, intakes, deadlines and sources.",
  robots: { index: false, follow: true },
};

export default async function ComparePage({
  searchParams,
}: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const raw = (Array.isArray(sp.slugs) ? sp.slugs[0] : sp.slugs) ?? "";
  const slugs = raw.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 4);

  return (
    <>
      <PageHeader eyebrow="Compare" title="Side by side, same definitions"
        description="The same field means the same thing in every column: tuition is annual, the all-in estimate includes living cost and insurance, and the requirement rows come from that programme's own record." />
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-10 sm:px-6">
        <CompareView initialSlugs={slugs} />
        <Disclaimer />
      </div>
    </>
  );
}
