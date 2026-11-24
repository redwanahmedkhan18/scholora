import type { Metadata } from "next";
import { CounselorDashboard } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "My desk", robots: { index: false, follow: false } };

export default function Page() {
  return <CounselorDashboard />;
}
