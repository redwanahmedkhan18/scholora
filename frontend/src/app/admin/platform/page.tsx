import type { Metadata } from "next";
import { PlatformConfig } from "@/components/admin/platform";

export const metadata: Metadata = { title: "Platform settings", robots: { index: false, follow: false } };

export default function Page() {
  return <PlatformConfig />;
}
