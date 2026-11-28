import type { Metadata } from "next";
import PortalHome from "@/components/portal-home";

export const metadata: Metadata = {
  title: "My Scholara portal",
  description: "Your applications, documents, shortlist and updates in one place.",
  robots: { index: false, follow: false, nocache: true, googleBot: { index: false, follow: false } },
  referrer: "no-referrer",
};

export default function PortalPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10">
      <PortalHome />
    </div>
  );
}
