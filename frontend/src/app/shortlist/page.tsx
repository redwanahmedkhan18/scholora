import type { Metadata } from "next";
import ShortlistView from "@/components/shortlist-view";
import { PageHeader } from "@/components/ui";

export const metadata: Metadata = {
  title: "My shortlist",
  description: "Programmes, universities and scholarships you saved while researching.",
  robots: { index: false, follow: true },
};

export default function ShortlistPage() {
  return (
    <>
      <PageHeader eyebrow="Shortlist" title="What you saved"
        description="Everything you starred while researching. Compare the programmes side by side, then bring the list to a counseling session." />
      <div className="mx-auto max-w-4xl px-4 py-10 sm:px-6">
        <ShortlistView />
      </div>
    </>
  );
}
