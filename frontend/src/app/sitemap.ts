import type { MetadataRoute } from "next";
import { apiOrNull, getSettings, publishedOrigin } from "@/lib/api";

type Dict = Record<string, any>;

export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [settings, data] = await Promise.all([getSettings(), apiOrNull<Dict>("/sitemap-data", 600)]);
  const base = publishedOrigin(settings).replace(/\/$/, "");
  const now = new Date();

  const statics: MetadataRoute.Sitemap = [
    ["", 1.0, "daily"], ["/study-destinations", 0.9, "weekly"], ["/universities", 0.9, "daily"],
    ["/programs", 0.9, "daily"], ["/scholarships", 0.85, "weekly"], ["/phd", 0.8, "weekly"],
    ["/services", 0.8, "monthly"], ["/success-stories", 0.7, "weekly"], ["/events", 0.7, "daily"],
    ["/blog", 0.7, "weekly"], ["/faqs", 0.6, "monthly"], ["/contact", 0.7, "monthly"],
    ["/appointments", 0.7, "monthly"], ["/profile-assessment", 0.9, "monthly"],
  ].map(([url, priority, changeFrequency]) => ({
    url: `${base}${url}`, lastModified: now, priority: priority as number,
    changeFrequency: changeFrequency as MetadataRoute.Sitemap[number]["changeFrequency"],
  }));

  if (!data) return statics;

  const entry = (path: string, updated?: string, priority = 0.6): MetadataRoute.Sitemap[number] => ({
    url: `${base}${path}`,
    lastModified: updated ? new Date(updated) : now,
    changeFrequency: "weekly",
    priority,
  });

  return [
    ...statics,
    ...(data.countries ?? []).map((c: Dict) => entry(`/study-destinations/${c.slug}`, c.updated, 0.85)),
    ...(data.universities ?? []).map((u: Dict) => entry(`/universities/${u.country}/${u.slug}`, u.updated, 0.8)),
    ...(data.programs ?? []).map((p: Dict) => entry(`/programs/${p.slug}`, p.updated, 0.75)),
    ...(data.scholarships ?? []).map((s: Dict) => entry(`/scholarships/${s.slug}`, s.updated, 0.7)),
    ...(data.faculty ?? []).map((f: Dict) => entry(`/faculty/${f.slug}`, f.updated, 0.6)),
    // Academic divisions, distinct from the professors above.
    ...(data.faculties ?? []).map((f: Dict) => entry(`/faculties/${f.slug}`, f.updated, 0.65)),
    ...(data.services ?? []).map((s: Dict) => entry(`/services/${s.slug}`, s.updated, 0.65)),
    ...(data.success_stories ?? []).map((s: Dict) => entry(`/success-stories/${s.slug}`, s.updated, 0.55)),
    ...(data.posts ?? []).map((p: Dict) => entry(`/blog/${p.slug}`, p.updated, 0.6)),
  ];
}
