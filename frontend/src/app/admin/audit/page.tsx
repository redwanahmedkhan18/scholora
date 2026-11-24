import type { Metadata } from "next";
import { AuditLogViewer } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "Audit log", robots: { index: false, follow: false } };

export default function Page() {
  return <AuditLogViewer />;
}
