import type { Metadata } from "next";
import { DocumentQueue } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "Document review", robots: { index: false, follow: false } };

export default function Page() {
  return <DocumentQueue />;
}
