import type { Metadata } from "next";
import { SecurityPanel } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "My security", robots: { index: false, follow: false } };

export default function Page() {
  return <SecurityPanel />;
}
