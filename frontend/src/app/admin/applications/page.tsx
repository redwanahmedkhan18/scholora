import type { Metadata } from "next";
import { ApplicationsBoard } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "Applications", robots: { index: false, follow: false } };

export default function Page() {
  return <ApplicationsBoard />;
}
