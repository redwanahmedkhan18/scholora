import type { Metadata } from "next";
import PortalView from "@/components/portal-view";

export const metadata: Metadata = {
  title: "My Scholara portal",
  description: "Your applications, documents, shortlist and updates in one place.",
  // A private, token-addressed page must never be indexed or sent to a third party.
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "no-referrer",
};

export default async function PortalPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <PortalView token={token} consumeToken />
    </div>
  );
}
