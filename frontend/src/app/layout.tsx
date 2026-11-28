import type { Metadata, Viewport } from "next";
import "./globals.css";
import { apiOrNull, getSettings, publishedOrigin } from "@/lib/api";
import { contactFromSettings, safeExternalHref } from "@/lib/contact";
import SiteHeader from "@/components/site-header";
import Analytics from "@/components/analytics";
import { BottomNav, SiteFooter, WhatsAppButton } from "@/components/site-chrome";
import AssistantWidget from "@/components/assistant";
import Providers from "@/components/providers";
import { jsonLdScript } from "@/lib/json-ld";

export async function generateMetadata(): Promise<Metadata> {
  const s = await getSettings();
  const title = s.default_seo_title ?? "Scholara | Study Abroad Consultancy in Uttara, Dhaka";
  const description = s.default_seo_description ?? "Verified university, programme, scholarship and faculty data for Bangladeshi students.";
  return {
    metadataBase: new URL(publishedOrigin(s)),
    title: { default: title, template: `%s | ${s.brand_name ?? "Scholara"}` },
    description,
    applicationName: s.brand_name ?? "Scholara",
    keywords: [
      "study abroad consultancy Dhaka", "study abroad Uttara", "study in USA from Bangladesh",
      "UK university application Bangladesh", "scholarships for Bangladeshi students",
      "PhD supervisor search", "IELTS requirement university", "education consultancy Bangladesh",
    ],
    authors: [{ name: s.brand_name ?? "Scholara" }],
    alternates: { canonical: "/" },
    openGraph: {
      type: "website", siteName: s.brand_name ?? "Scholara", title, description,
      locale: "en_US", url: publishedOrigin(s),
    },
    twitter: { card: "summary_large_image", title, description },
    robots: { index: true, follow: true, googleBot: { index: true, follow: true, "max-image-preview": "large", "max-snippet": -1 } },
    verification: s.google_site_verification ? { google: s.google_site_verification } : undefined,
    icons: {
      icon: [{ url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%230f3d8a'/%3E%3Ctext x='16' y='22' font-size='18' font-family='sans-serif' font-weight='bold' fill='white' text-anchor='middle'%3ES%3C/text%3E%3C/svg%3E" }],
    },
  };
}

export const viewport: Viewport = {
  themeColor: "#0f3d8a",
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [settings, countries] = await Promise.all([
    getSettings(),
    apiOrNull<any[]>("/countries", 300).then((c) => c ?? []),
  ]);

  const contact = contactFromSettings(settings);

  const org = {
    "@context": "https://schema.org",
    "@type": settings.organization_type || "EducationalOrganization",
    name: settings.brand_name ?? "Scholara",
    description: settings.about_short,
    url: publishedOrigin(settings),
    foundingDate: settings.founding_year ? String(settings.founding_year) : undefined,
    address: {
      "@type": "PostalAddress",
      streetAddress: settings.office_address,
      addressLocality: "Uttara, Dhaka",
      addressRegion: "Dhaka",
      postalCode: "1230",
      addressCountry: "BD",
    },
    telephone: contact.phone ?? undefined,
    email: contact.email ?? undefined,
    openingHours: settings.office_hours,
    sameAs: [contact.facebookUrl, settings.instagram_url, settings.linkedin_url, settings.youtube_url]
      .map((u) => safeExternalHref(u))
      .filter(Boolean),
    areaServed: "Bangladesh",
  };

  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col antialiased">
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdScript(org) }} />
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-[60] focus:rounded-lg focus:bg-brand-800 focus:px-4 focus:py-2 focus:text-white">
          Skip to content
        </a>
        <Providers>
          <SiteHeader settings={settings} countries={countries} />
          <main id="main" className="flex-1">{children}</main>
          <SiteFooter settings={settings} countries={countries} />
          <AssistantWidget settings={settings} />
          <WhatsAppButton settings={settings} />
          <BottomNav settings={settings} />
          <Analytics settings={settings} />
        </Providers>
      </body>
    </html>
  );
}
