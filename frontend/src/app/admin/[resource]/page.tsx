import { notFound } from "next/navigation";
import ResourceManager from "@/components/admin/resource-manager";
import { RESOURCE_META } from "@/lib/admin-meta";

export default async function AdminResourcePage({ params }: { params: Promise<{ resource: string }> }) {
  const { resource } = await params;
  if (!(resource in RESOURCE_META)) notFound();
  return <ResourceManager resource={resource} />;
}
