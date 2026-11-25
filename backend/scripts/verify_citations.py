#!/usr/bin/env python3
"""
Re-read every cited page and check whether it still says what we recorded.

The point of this script is NOT to decide whether data is correct - a machine cannot
do that, and pretending otherwise is how wrong figures acquire a green tick. The point
is to narrow the human's job. Someone re-verifying 104 pages by hand has to re-read
everything. After this runs they only have to look at the handful of figures the page
no longer appears to contain.

Classification per cited field:

  CONFIRMED     the recorded value was found in the page text. Weak evidence - "600"
                appears on plenty of pages for unrelated reasons - but its absence is
                far more informative than its presence, which is why NOT_FOUND is the
                output that matters.
  NOT_FOUND     the page fetched fine and the value is not in it. This is the queue a
                human should work through: either the figure changed, or it was wrong,
                or it lives behind a tab/PDF the fetcher cannot see.
  UNCHECKABLE   the page could not be read (403/429/timeout/DNS). Says nothing about
                the data - only that this route cannot confirm it.
  NOT_APPLICABLE  free prose, URLs, and similar fields that string matching cannot
                judge. Excluded from the score rather than silently counted as passes.

Exit code is always 0: a disagreement is a work item, not a build failure.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.crm import DataVerification  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 ScholaraCitationCheck/1.0")

# Only these fields are checked, and the list is deliberately an allowlist rather than
# a blocklist of things to skip.
#
# The verification table records provenance for every column, including internal
# bookkeeping - is_published, slug, priority, status. No university page will ever
# contain the string "is_published = True", so checking those produces a flood of
# NOT_FOUND rows that are all noise. A report where most entries are false alarms is a
# report nobody reads, which is worse than no report, because it looks like diligence.
#
# What belongs here is the set of fields that assert something about the outside world
# and that a page could reasonably state: money, test scores, dates, durations, counts.
# Those are also precisely the fields that hurt a student if they are stale.
CHECKABLE_FIELDS = {
    # money
    "tuition_annual", "tuition_min", "tuition_max", "living_cost_min", "living_cost_max",
    "amount_value", "stipend", "currency",
    # entry requirements
    "ielts_overall", "toefl_ibt", "min_cgpa", "cgpa_scale", "min_qualification",
    # structure and timing
    "duration_months", "established_year", "year", "month", "term", "intake",
    # placement
    "city", "country",
}
# Deliberately excluded even though they look checkable: study_mode, degree_level, term,
# language, institution_type, currency. These store our internal vocabulary (FULL_TIME,
# BACHELOR, MARCH, KRW) while the page writes "full-time", "undergraduate", "Spring", a
# won symbol. Matching our enum against their prose measures nothing.


def strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"'),
                    ("&euro;", "EUR"), ("&pound;", "GBP"), ("&dollar;", "$")):
        text = text.replace(ent, ch)
    return re.sub(r"\s+", " ", text)


def candidates(value: str) -> List[str]:
    """
    Ways the same fact can legitimately be written on a page. A tuition figure stored
    as 12500.0 may appear as "12,500" or "12 500" or "EUR 12.500"; refusing to match
    those would fill the report with false alarms and the human would stop reading it.
    """
    v = (value or "").strip()
    if not v:
        return []
    out = {v, v.lower()}
    num = re.fullmatch(r"-?\d+(?:\.\d+)?", v)
    if num:
        f = float(v)
        if f.is_integer():
            i = int(f)
            out |= {str(i), f"{i:,}", f"{i:,}".replace(",", " "), f"{i:,}".replace(",", ".")}
            # IELTS 6.0 is written "6" as often as "6.0"; both are the same requirement.
            out.add(f"{i}.0")
            if i >= 1000:                      # "12.5k" style shorthand
                out.add(f"{i // 1000},{i % 1000:03d}")
        else:
            out.add(f"{f:g}")
    # Dates: 2026-09-01 is almost never printed in ISO form on a university page.
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if iso:
        y, m, d = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        try:
            dt = datetime(y, m, d)
            out |= {dt.strftime("%d %B %Y"), dt.strftime("%B %d, %Y"), dt.strftime("%d/%m/%Y"),
                    dt.strftime("%m/%d/%Y"), dt.strftime("%-d %B %Y"), dt.strftime("%B %Y")}
        except ValueError:
            pass
    return [c for c in out if len(c) >= 2]


def is_bare_homepage(url: str) -> bool:
    """
    True for https://en.snu.ac.kr/ and similar - a host with no meaningful path.

    Citing a homepage for a specific figure is only marginally better than citing
    nothing. The spec requires a per-field source URL so a reviewer can re-check the
    claim; a homepage sends them hunting through a whole site, and it will still
    resolve 200 forever, so a link checker will never complain about it.
    """
    m = re.match(r"https?://[^/]+(/.*)?$", url or "")
    if not m:
        return False
    path = (m.group(1) or "/").split("?")[0].split("#")[0].rstrip("/")
    return path in ("", "/index.html", "/en", "/english", "/home")


def classify_http(exc: Exception | None, status: int | None) -> Tuple[bool, str]:
    if exc is not None:
        return False, type(exc).__name__
    if status in (404, 410):
        return False, f"HTTP {status} (page gone)"
    if status and status >= 400:
        return False, f"HTTP {status}"
    return True, f"HTTP {status}"


def fetch(url: str) -> Dict[str, Any]:
    try:
        with httpx.Client(follow_redirects=True, timeout=25.0,
                          headers={"User-Agent": UA, "Accept-Language": "en"}) as c:
            r = c.get(url)
            ok, note = classify_http(None, r.status_code)
            return {"url": url, "ok": ok, "note": note,
                    "text": strip_html(r.text) if ok else ""}
    except Exception as exc:                                  # noqa: BLE001
        ok, note = classify_http(exc, None)
        return {"url": url, "ok": ok, "note": note, "text": ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../../docs/citation-diff.md")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="only the first N URLs (smoke test)")
    args = ap.parse_args()

    db = SessionLocal()
    rows = db.query(DataVerification).filter(DataVerification.source_url != "").all()
    by_url: Dict[str, List[DataVerification]] = defaultdict(list)
    for r in rows:
        if r.source_url:  # the column is nullable; a None key would merge unrelated rows
            by_url[r.source_url].append(r)
    urls = sorted(by_url)
    if args.limit:
        urls = urls[:args.limit]
    print(f"{len(rows)} cited facts across {len(by_url)} URLs; fetching {len(urls)}...")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pages = {p["url"]: p for p in pool.map(fetch, urls)}

    results: List[Dict[str, Any]] = []
    for url in urls:
        page = pages[url]
        haystack = page["text"].lower()
        # De-duplicate: the same figure cited by 40 rows is one thing to check, not 40.
        seen: set[Tuple[str, str]] = set()
        for r in by_url[url]:
            field, value = r.field_name, (r.field_value or "").strip()
            if (field, value) in seen:
                continue
            seen.add((field, value))
            if not value or field not in CHECKABLE_FIELDS or len(value) > 60:
                verdict = "NOT_APPLICABLE"
            elif is_bare_homepage(url):
                verdict = "CITATION_TOO_COARSE"
            elif not page["ok"]:
                verdict = "UNCHECKABLE"
            else:
                verdict = ("CONFIRMED"
                           if any(c.lower() in haystack for c in candidates(value))
                           else "NOT_FOUND")
            results.append({"url": url, "entity": r.entity_type, "entity_id": r.entity_id,
                            "field": field, "value": value, "verdict": verdict,
                            "note": page["note"], "status": r.status,
                            "verified_by": r.verified_by or "-"})

    tally: Dict[str, int] = defaultdict(int)
    for x in results:
        tally[x["verdict"]] += 1
    checked = tally["CONFIRMED"] + tally["NOT_FOUND"]
    pct = (100.0 * tally["CONFIRMED"] / checked) if checked else 0.0

    out = [
        "# Citation diff report",
        "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} by "
        "`backend/scripts/verify_citations.py`._",
        "",
        "This report does **not** certify any figure as correct. Only a human reading the",
        "page can do that. What it does is shrink the job: instead of re-reading every",
        "cited page, a reviewer works the **NOT_FOUND** table below, where the page no",
        "longer appears to contain the value we stored.",
        "",
        "| Verdict | Count | Meaning |",
        "|---|---:|---|",
        f"| CONFIRMED | {tally['CONFIRMED']} | value found in the page text |",
        f"| NOT_FOUND | {tally['NOT_FOUND']} | page read fine, value absent - **review these** |",
        f"| UNCHECKABLE | {tally['UNCHECKABLE']} | page blocked or unreachable from here |",
        f"| CITATION_TOO_COARSE | {tally['CITATION_TOO_COARSE']} | cited page is a site homepage, not the page stating the figure |",
        f"| NOT_APPLICABLE | {tally['NOT_APPLICABLE']} | internal bookkeeping or prose; not a claim a page would state |",
        "",
        f"**{tally['CONFIRMED']} of {checked} machine-checkable facts ({pct:.1f}%) were "
        f"found verbatim on their cited page.**",
        "",
        f"That percentage is the less important number. The one to act on is "
        f"**{tally['CITATION_TOO_COARSE']}**: world-claims - tuition, IELTS scores, "
        f"deadlines - whose citation points at a site homepage. Those are not "
        f"re-checkable by anyone, and because a homepage never returns 404 they score a "
        f"clean bill of health from any link checker. A green link report on this "
        f"dataset was measuring reachability and being read as verifiability.",
        "",
        "## NOT_FOUND - needs a human",
        "",
        "Most entries here are not errors. The usual causes, in rough order of frequency:",
        "the figure sits behind a tab or in a PDF the fetcher cannot read; the page shows a",
        "range where we stored a point value; or the university genuinely changed it. The",
        "third case is the reason this report exists.",
        "",
    ]
    nf = [x for x in results if x["verdict"] == "NOT_FOUND"]
    if nf:
        out += ["| Entity | id | Field | Stored value | Cited page |", "|---|---:|---|---|---|"]
        for x in sorted(nf, key=lambda y: (y["entity"], y["field"])):
            out.append(f"| {x['entity']} | {x['entity_id']} | `{x['field']}` | "
                       f"`{x['value']}` | <{x['url']}> |")
    else:
        out.append("_None._")

    out += ["", "## CITATION_TOO_COARSE - cited page cannot show the figure", "",
            "A specific number cited to a university homepage cannot be re-checked by",
            "anyone, human or machine: the homepage does not state tuition, IELTS or",
            "intake months. These will also pass a link checker forever, because the URL",
            "resolves perfectly - which is exactly why they need listing separately.",
            "",
            "The fix is to deepen the citation to the fees/admissions page that states the",
            "figure, not to delete the data.",
            ""]
    coarse = sorted({(x["entity"], x["field"], x["url"])
                     for x in results if x["verdict"] == "CITATION_TOO_COARSE"})
    if coarse:
        out += ["| Entity | Field | Cited page |", "|---|---|---|"]
        out += [f"| {e} | `{f}` | <{u}> |" for e, f, u in coarse]
    else:
        out.append("_None._")

    out += ["", "## UNCHECKABLE - cannot be reached from here", "",
            "These say nothing about the data. A blocked page is a property of the",
            "publisher's bot policy, not of our figures; they must be checked in a browser.",
            ""]
    unreachable = sorted({(x["url"], x["note"]) for x in results if x["verdict"] == "UNCHECKABLE"})
    if unreachable:
        out += ["| Cited page | Why |", "|---|---|"]
        out += [f"| <{u}> | {n} |" for u, n in unreachable]
    else:
        out.append("_None._")

    out += ["", "## Per-page summary", "",
            "| Cited page | Confirmed | Not found | Fetch |", "|---|---:|---:|---|"]
    for url in urls:
        rs = [x for x in results if x["url"] == url]
        c = sum(1 for x in rs if x["verdict"] == "CONFIRMED")
        n = sum(1 for x in rs if x["verdict"] == "NOT_FOUND")
        out.append(f"| <{url}> | {c} | {n} | {pages[url]['note']} |")
    out.append("")

    dest = os.path.join(os.path.dirname(__file__), args.out)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        fh.write("\n".join(out))
    print(f"CONFIRMED {tally['CONFIRMED']}  NOT_FOUND {tally['NOT_FOUND']}  "
          f"TOO_COARSE {tally['CITATION_TOO_COARSE']}  "
          f"UNCHECKABLE {tally['UNCHECKABLE']}  N/A {tally['NOT_APPLICABLE']}")
    print(f"wrote {os.path.realpath(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
