import type { Metadata } from "next";
import { DataWorkbench } from "@/components/admin/data-workbench";

export const metadata: Metadata = { title: "Data import & verification", robots: { index: false, follow: false } };

export default function Page() {
  return <DataWorkbench />;
}
