/**
 * L-2: JSON-LD must not be able to end its own script element.
 *
 * `JSON.stringify` escapes quotes but not `<`, so a string containing `</script>` closes the
 * element early and the remainder is parsed as markup - on a public, server-rendered page, with
 * catalogue text that arrives from imported third-party pages and admin forms.
 *
 * These tests import the helper directly and also assert that every JSON-LD sink uses it, because
 * a helper nobody calls would pass the first half and protect nothing.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { jsonLdScript } from "../src/lib/json-ld.ts";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "src");

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (full.endsWith(".tsx") || full.endsWith(".ts")) out.push(full);
  }
  return out;
}

test("a script-terminating catalogue string cannot close the element", () => {
  const hostile = {
    "@context": "https://schema.org",
    name: "</script><script>window.__pwned=1</script>",
    description: "Tuition <b>bold</b> & \"quoted\" — ünïcode ✈",
  };
  const serialised = jsonLdScript(hostile);

  assert.ok(!serialised.includes("</script"), "the closing tag survived serialisation");
  assert.ok(!serialised.includes("<script"), "an opening tag survived serialisation");
  assert.ok(!serialised.includes("<"), "no raw '<' may reach the document");

  // It is still the same JSON: escaping '<' as \u003c is a no-op for any JSON parser.
  const roundTripped = JSON.parse(serialised);
  assert.deepEqual(roundTripped, hostile, "escaping must not change the data");
});

test("unicode, quotes and line separators survive as valid JSON", () => {
  const data = { name: "Université ✈ 北京大学", note: "line\u2028break\u2029here", q: '"quoted"' };
  const serialised = jsonLdScript(data);
  assert.deepEqual(JSON.parse(serialised), data);
  assert.ok(!serialised.includes("\u2028") && !serialised.includes("\u2029"),
    "a raw line separator is not valid inside a script element");
});

test("ordinary structured data is unchanged by the escaping", () => {
  const data = { "@type": "Organization", name: "Scholara", url: "https://scholara.com.bd" };
  assert.equal(jsonLdScript(data), JSON.stringify(data));
});

test("every JSON-LD sink serialises through the helper", () => {
  const offenders: string[] = [];
  for (const file of walk(src)) {
    const text = readFileSync(file, "utf8");
    for (const line of text.split("\n")) {
      if (!line.includes("dangerouslySetInnerHTML")) continue;
      if (!line.includes("application/ld+json") && !line.includes("__html")) continue;
      // A JSON-LD sink must not stringify directly.
      if (line.includes("JSON.stringify(")) offenders.push(`${file}: ${line.trim()}`);
    }
  }
  assert.deepEqual(offenders, [], `these sinks bypass jsonLdScript:\n${offenders.join("\n")}`);
});

test("the helper is used at least once per JSON-LD page family", () => {
  const sinks = walk(src).filter((file) =>
    readFileSync(file, "utf8").includes("application/ld+json"));
  assert.ok(sinks.length >= 10, `expected several JSON-LD pages, found ${sinks.length}`);
  for (const file of sinks) {
    assert.ok(readFileSync(file, "utf8").includes("jsonLdScript("),
      `${file} renders JSON-LD but never calls jsonLdScript`);
  }
});
