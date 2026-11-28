import type { MetadataRoute } from "next";
import { getSettings, publishedOrigin } from "@/lib/api";

export const revalidate = 3600;

export default async function robots(): Promise<MetadataRoute.Robots> {
  const settings = await getSettings();
  const base = publishedOrigin(settings).replace(/\/$/, "");
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/admin", "/admin/", "/brochure/", "/api/", "/search", "/shortlist", "/compare"],
      },
    ],
    sitemap: `${base}/sitemap.xml`,
    host: base,
  };
}
