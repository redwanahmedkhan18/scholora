import type { Metadata } from "next";
import ExtractionWorkbench from "@/components/admin/extraction";

export const metadata: Metadata = {
  title: "Read a university website",
  robots: { index: false, follow: false },
};

export default function Page() {
  return <ExtractionWorkbench />;
}
