"use client";

import Script from "next/script";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { captureUtm, sanitizeAnalyticsPath, sessionId, track } from "@/lib/track";

type Dict = Record<string, any>;

/**
 * Google (GA4 / GTM / Ads) + Meta Pixel + first-party page tracking.
 * IDs come from the admin-editable site settings, never from hard-coded constants.
 * Nothing loads until the corresponding ID exists, so a fresh install stays clean.
 */
export default function Analytics({ settings }: { settings: Dict }) {
  const pathname = usePathname();
  const ga4 = settings.ga4_measurement_id as string | undefined;
  const gtm = settings.gtm_container_id as string | undefined;
  const pixel = settings.meta_pixel_id as string | undefined;
  const placeholder = (v?: string) => !v || /X{4,}|0{6,}/.test(v);

  // Never inject GA/GTM/Meta on a private portal route; a tag manager can observe
  // document.location even when this component makes no explicit call.
  const privateRoute = sanitizeAnalyticsPath(pathname) === null;

  useEffect(() => { captureUtm(); }, []);

  useEffect(() => {
    // Private portal routes are excluded from every analytics destination, including direct
    // third-party calls that do not pass through track().
    if (privateRoute) return;
    sessionId();
    track("page_view", { path: pathname });
    window.gtag?.("event", "page_view", { page_path: pathname });
    window.fbq?.("track", "PageView");
  }, [pathname, privateRoute]);

  if (privateRoute) return null;

  return (
    <>
      {!placeholder(ga4) && (
        <>
          <Script src={`https://www.googletagmanager.com/gtag/js?id=${ga4}`} strategy="afterInteractive" />
          <Script id="ga4-init" strategy="afterInteractive">
            {`window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
window.gtag=gtag;gtag('js',new Date());gtag('config','${ga4}',{send_page_view:false});
${settings.google_ads_conversion_id && !/X{4,}/.test(settings.google_ads_conversion_id) ? `gtag('config','${settings.google_ads_conversion_id}');` : ""}`}
          </Script>
        </>
      )}

      {!placeholder(gtm) && (
        <Script id="gtm-init" strategy="afterInteractive">
          {`(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});
var f=d.getElementsByTagName(s)[0],j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';
j.async=true;j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);})(window,document,'script','dataLayer','${gtm}');`}
        </Script>
      )}

      {!placeholder(pixel) && (
        <Script id="meta-pixel" strategy="afterInteractive">
          {`!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script',
'https://connect.facebook.net/en_US/fbevents.js');fbq('init','${pixel}');`}
        </Script>
      )}
    </>
  );
}
