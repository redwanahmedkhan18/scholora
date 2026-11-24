import type { Metadata } from "next";
import ReadinessWorkbench from "@/components/admin/readiness";

export const metadata: Metadata = {
  title: "Go-live & verification",
  robots: { index: false, follow: false },
};

export default function Page() {
  return <ReadinessWorkbench />;
}
