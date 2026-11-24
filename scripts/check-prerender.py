#!/usr/bin/env python3
"""
Fail the build gate if any page was prerendered as an error page.

Why this exists
---------------
A static page records whatever the API answered at build time, and that answer is then
served to every visitor until the next revalidation. So a transient build-time failure -
a 429 from our own rate limiter, a backend restart, a slow query - does not surface as a
build error. It surfaces months later as a university page that quietly says "not found"
while the record exists happily in the database.

That exact failure shipped once: the production build issued more requests per minute than
the public rate limit allowed, the limiter refused three university pages, and the frontend
helper treated 429 the same as 404. Three real universities became permanent 404s with a
completely green build log.

Two guards now prevent it. `apiOrNull` throws on anything that is not a clean 404 so the
build dies loudly, and the website sends an internal token that exempts first-party
rendering from the limiter. This script is the backstop that proves the result: it reads
the status Next recorded for every prerendered page and refuses any unexpected error.

Usage:  python3 scripts/check-prerender.py [frontend_dir]
"""
from __future__ import annotations

import json
import pathlib
import sys

# Pages that are *supposed* to be error pages - Next prerenders these by design.
EXPECTED_ERROR_PAGES = {"/_not-found", "/_global-error", "/404", "/500"}


def main(frontend: pathlib.Path) -> int:
    root = frontend / ".next" / "server" / "app"
    if not root.is_dir():
        print(f"✗ no build output at {root} - run `npm run build` first")
        return 1

    scanned, unexpected = 0, []
    for meta_file in sorted(root.rglob("*.meta")):
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue  # not a page meta file

        scanned += 1
        status = int(meta.get("status", 200))
        route = "/" + str(meta_file.relative_to(root)).removesuffix(".meta")
        if status >= 400 and route not in EXPECTED_ERROR_PAGES:
            unexpected.append((status, route))

    print(f"prerendered pages scanned : {scanned}")
    print(f"frozen error pages        : {len(unexpected)}")

    if unexpected:
        print("\n✗ these pages were baked as errors and will serve that error to users:")
        for status, route in sorted(unexpected):
            print(f"    {status}  {route}")
        print("\n  Likely cause: the API refused or failed during the build.")
        print("  Check INTERNAL_API_TOKEN matches between backend and frontend/.env.local,")
        print("  confirm the API was up for the whole build, then rebuild.")
        return 1

    if scanned == 0:
        print("\n✗ no pages found - the build output looks empty")
        return 1

    print("\n✓ every prerendered page carries a success status")
    return 0


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "frontend")
    raise SystemExit(main(target.resolve()))
