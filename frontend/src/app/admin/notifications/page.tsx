import type { Metadata } from "next";
import { NotificationInbox } from "@/components/admin/workflow";

export const metadata: Metadata = { title: "Notifications", robots: { index: false, follow: false } };

export default function Page() {
  return <NotificationInbox />;
}
