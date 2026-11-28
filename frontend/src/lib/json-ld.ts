/**
 * Safe JSON-LD for a `<script type="application/ld+json">` element.
 *
 * `JSON.stringify` is not sufficient here. It escapes quotes and backslashes, but not `<`, so a
 * string containing `</script>` terminates the script element early and everything after it is
 * parsed as markup. Catalogue text is imported from third-party pages and edited through the admin
 * forms, so a `</script><script>…` sequence in a university name, a programme overview or a faculty
 * blurb would become same-origin script on a public, server-rendered page.
 *
 * The fix is the standard one: escape the characters that can end a script element or be read as a
 * line terminator by a JavaScript parser, using their JSON unicode escapes. The output is still
 * valid JSON - `\u003c` is `<` to any JSON parser - so search engines and validators see exactly
 * the same structured data as before.
 */
export function jsonLdScript(data: unknown): string {
  return JSON.stringify(data)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}
