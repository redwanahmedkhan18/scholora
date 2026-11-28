/**
 * The Content-Security-Policy, kept in one place so the header that ships and the header the tests
 * check cannot drift apart.
 *
 * WHY THIS FILE IS PLAIN COMMONJS
 * -------------------------------
 * Same constraint as `config/api-base.js`: `next.config.ts` is compiled and `require()`d at build
 * time, and Node resolves that require to `.js`/`.json`/`.node` only. A `.js` file in a package
 * without `"type": "module"` is CommonJS, so both the config and the test suite can load it on any
 * Node version. JSDoc types keep the TypeScript side checked (`allowJs` is on).
 *
 * WHAT IT IS FOR — AND WHAT IT IS NOT
 * -----------------------------------
 * This header is **defence in depth, not the fix** for the blog XSS (F-1). The fix is that
 * `src/lib/markdown.ts` renders a parsed tree with React and never builds markup, so untrusted text
 * cannot become an element or an attribute at all.
 *
 * Being precise about the limits matters more than the header looking strong:
 *
 * * `script-src` has to include `'unsafe-inline'`. Next's App Router ships its hydration payload in
 *   inline `self.__next_f.push(...)` scripts, and the optional GA4/GTM/Meta bootstrap is inline too
 *   (configured by staff, not by blog content). A nonce-based policy would need every route
 *   rendered per request; this site is served as static/ISR pages whose inline scripts carry no
 *   nonce, so a nonce policy would block the application's own bootstrap. `'unsafe-inline'` is
 *   therefore *stated* here rather than quietly avoided — and for the same reason this policy is
 *   not claimed to stop an injected inline script from running.
 * * `script-src-attr 'none'` is the part of `script-src` that *does* bite: inline event-handler
 *   attributes (`onmouseover="…"`) are blocked outright, independently of `'unsafe-inline'`, in
 *   every browser that implements CSP Level 3. That is the second F-1 vector — the attribute
 *   breakout — and it is also the vector a future rendering mistake is most likely to reintroduce.
 *   React attaches handlers with `addEventListener`, never as attributes, so nothing legitimate
 *   depends on the allowance this removes.
 * * `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'` and `form-action 'self'`
 *   close plugin, `<base>`-hijack, framing and form-retarget classes of injection, none of which
 *   any part of this application needs to be open.
 * * `'unsafe-eval'` is never granted in production. `next dev` uses eval-based source maps, so
 *   development allows it explicitly and production does not.
 *
 * Analytics origins are listed because the application embeds GA4/GTM and the Meta pixel when the
 * operator configures them. Narrowing further would break those integrations; broadening to `*`
 * would make the header decorative. `frame-src` allows https because the contact page embeds an
 * operator-configured map URL, and an iframe of another origin cannot reach this origin.
 */

"use strict";

/** Third-party script hosts the application can be configured to load (see components/analytics). */
const SCRIPT_HOSTS = ["https://www.googletagmanager.com", "https://connect.facebook.net"];

/** Where those scripts send their beacons. */
const CONNECT_HOSTS = [
  "https://www.googletagmanager.com",
  "https://www.google-analytics.com",
  "https://region1.google-analytics.com",
  "https://analytics.google.com",
  "https://connect.facebook.net",
  "https://www.facebook.com",
];

/**
 * Build the policy value for one environment.
 *
 * @param {{ isDev?: boolean }} [options]
 * @returns {string}
 */
function buildCsp(options) {
  const isDev = Boolean(options && options.isDev);
  const scriptSrc = ["'self'", "'unsafe-inline'", ...SCRIPT_HOSTS];
  const connectSrc = ["'self'", ...CONNECT_HOSTS];
  if (isDev) {
    // Only `next dev` needs these: eval-based source maps, and the hot-reload websocket.
    scriptSrc.push("'unsafe-eval'");
    connectSrc.push("ws:", "wss:");
  }

  /** @type {[string, string[]][]} */
  const directives = [
    ["default-src", ["'self'"]],
    ["script-src", scriptSrc],
    // Inline event-handler attributes are never how this application works, and blocking them
    // removes the execution path an attribute-breakout payload needs.
    ["script-src-attr", ["'none'"]],
    // Tailwind ships a stylesheet, but React's inline `style={{…}}` props are attributes too.
    ["style-src", ["'self'", "'unsafe-inline'"]],
    // `data:`/`blob:` for the QR-code and favicon data URIs the app renders itself.
    ["img-src", ["'self'", "data:", "blob:", "https:"]],
    ["font-src", ["'self'", "data:"]],
    ["connect-src", connectSrc],
    // The contact page embeds an operator-configured map. A cross-origin frame cannot read this
    // origin; the alternative (a per-provider allowlist) would break on any other embed URL.
    ["frame-src", ["'self'", "https:"]],
    ["media-src", ["'self'", "https:"]],
    ["object-src", ["'none'"]],
    ["base-uri", ["'self'"]],
    ["form-action", ["'self'"]],
    ["frame-ancestors", ["'none'"]],
    ["manifest-src", ["'self'"]],
    ["worker-src", ["'self'"]],
  ];
  if (!isDev) {
    // A production build is https-only; loopback development is not, and a browser that upgrades
    // http://localhost gets a dead dev server.
    directives.push(["upgrade-insecure-requests", []]);
  }
  return directives
    .map(([name, values]) => (values.length ? `${name} ${values.join(" ")}` : name))
    .join("; ");
}

module.exports = { buildCsp, SCRIPT_HOSTS, CONNECT_HOSTS };
