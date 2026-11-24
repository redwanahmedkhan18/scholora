import type { Metadata } from "next";
import { OperationsPanel } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "Operations", robots: { index: false, follow: false } };

export default function Page() {
  return <OperationsPanel />;
}
