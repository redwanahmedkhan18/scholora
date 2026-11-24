"""
Live extraction: read a university's own website and answer a question from it.

The operator supplies a university name, a country, and an instruction in plain words
("what is the tuition for the MSc in Data Science and when is the deadline?"). This
module resolves the institution to its official site, works out which pages are likely
to answer the question, fetches them, and returns findings.

Three rules shape every decision in here, and all three exist because this is the most
dangerous feature in the platform. Everything else in Scholara records where a figure
came from. An extractor that summarises a web page in its own words is precisely how an
invented tuition fee gets a source URL stapled to it and starts looking trustworthy.

  1. NEVER GUESS A DOMAIN. The site comes from our own verified catalogue record, or the
     operator types it in. We do not construct "www.<name>.edu" and hope. A plausible
     wrong domain is worse than no answer, because somebody will believe it.

  2. EVERY FINDING IS A VERBATIM QUOTE FROM A PAGE WE FETCHED. Not a paraphrase, not a
     summary, not a number lifted out of its sentence. The quote and the URL travel
     together so a human can click through and see the same words.

  3. THE LLM IS OPTIONAL AND IS NEVER TRUSTED. If a model is configured it is used only
     to pick which passages answer the question. Anything it returns is checked back
     against the fetched text, character for character; whatever is not found there is
     discarded and counted. A model cannot introduce a fact through this path, only fail
     to find one. With no model configured the deterministic path does the same job less
     elegantly, which is why the feature works with zero credentials.

Nothing here writes to the catalogue. Findings are staged for a human, reusing the
importer's existing review machinery.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import llm
from app.models.education import Country, University

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 ScholaraExtractor/1.0 "
              "(+https://scholara.com.bd/bots)")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
TIMEOUT = 20.0
MAX_PAGES = 6
MAX_PAGE_BYTES = 2_000_000
POLITE_DELAY = 0.4          # seconds between requests to the same host
SNIPPET_CHARS = 320

# ---------------------------------------------------------------------------- intents
# What the operator is asking for, the words a university uses when it answers, and the
# words that tend to appear in a link pointing at the page that answers it. Universities
# are remarkably consistent about this vocabulary, which is why a keyword approach gets
# useful results without a model.


@dataclass(frozen=True)
class Intent:
    name: str
    question_words: Tuple[str, ...]     # in the operator's instruction
    page_words: Tuple[str, ...]         # in the body of the answering page
    link_words: Tuple[str, ...]         # in the href or anchor text
    wants_number: bool = False


INTENTS: Tuple[Intent, ...] = (
    Intent("tuition",
           ("tuition", "fee", "fees", "cost", "price", "how much", "expensive", "afford"),
           ("tuition", "fee", "fees", "per year", "per semester", "per annum", "cost"),
           ("fee", "fees", "tuition", "cost", "finance", "funding"),
           wants_number=True),
    Intent("entry_requirements",
           ("requirement", "requirements", "entry", "eligib", "ielts", "toefl", "gpa",
            "cgpa", "english", "qualify", "admission criteria", "prerequisite"),
           ("ielts", "toefl", "entry requirement", "admission requirement", "gpa",
            "grade point", "english language", "minimum"),
           ("entry", "requirement", "admission", "apply", "eligibility", "english"),
           wants_number=True),
    Intent("deadlines",
           ("deadline", "when", "closing", "intake", "start date", "apply by", "semester",
            "application date"),
           ("deadline", "closing date", "applications open", "application period",
            "intake", "semester begins", "start of"),
           ("deadline", "date", "calendar", "apply", "admission", "intake"),
           wants_number=True),
    Intent("scholarships",
           ("scholarship", "funding", "bursary", "financial aid", "waiver", "stipend",
            "grant", "fellowship"),
           ("scholarship", "bursary", "financial aid", "fee waiver", "stipend", "grant"),
           ("scholarship", "funding", "financial", "bursary", "aid", "fees"),
           wants_number=False),
    Intent("programs",
           ("program", "programme", "course", "degree", "major", "msc", "bsc", "phd",
            "master", "bachelor", "study", "subject"),
           ("programme", "program", "degree", "master", "bachelor", "curriculum",
            "course structure", "credits"),
           ("programme", "program", "course", "study", "degree", "academic"),
           wants_number=False),
    Intent("contact",
           ("contact", "email", "phone", "address", "reach", "office", "enquir"),
           ("email", "telephone", "phone", "contact us", "@"),
           ("contact", "about", "enquiry", "help"),
           wants_number=False),
    Intent("application_process",
           ("how to apply", "application process", "apply", "documents", "submit",
            "procedure", "step"),
           ("how to apply", "application process", "required documents", "submit your",
            "application form", "supporting documents"),
           ("apply", "application", "admission", "how-to"),
           wants_number=False),
)
DEFAULT_INTENTS = ("programs", "entry_requirements", "tuition")


_WORD_RE: Dict[str, re.Pattern[str]] = {}


def has_word(haystack: str, word: str) -> bool:
    """
    Whole-word containment.

    Plain `in` matching looked fine until a course listing produced the tuition finding
    "The Lir Academy Costume Level 1" - because "cost" is a substring of "costume".
    Nonsense like that in front of a counsellor is worse than a missing answer: it is
    the kind of output that makes people stop trusting the tool entirely.
    """
    rx = _WORD_RE.get(word)
    if rx is None:
        # A trailing "s"/"es" is allowed so "scholarship" matches "scholarships" and
        # "fee" matches "fees" - but nothing longer, which is what keeps "cost" from
        # matching "costume" (a three-letter tail) while still matching "costs".
        rx = _WORD_RE[word] = re.compile(
            r"(?<![a-z0-9])" + re.escape(word) + r"(?:e?s)?(?![a-z0-9])")
    return bool(rx.search(haystack))


def classify_instruction(instruction: str) -> List[str]:
    """
    Work out what was asked. Falls back to a sensible default set rather than returning
    nothing, because an operator who types "tell me about this place" still deserves an
    answer.
    """
    text = (instruction or "").lower()
    hits = [i.name for i in INTENTS if any(has_word(text, w) for w in i.question_words)]
    return hits or list(DEFAULT_INTENTS)


def _intent(name: str) -> Optional[Intent]:
    return next((i for i in INTENTS if i.name == name), None)


# ------------------------------------------------------------------------- resolution
@dataclass
class Resolution:
    status: str                          # RESOLVED | NOT_IN_CATALOGUE | NO_WEBSITE
    university: Optional[University] = None
    website: str = ""
    message: str = ""
    candidates: List[Dict[str, str]] = dc_field(default_factory=list)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def resolve(db: Session, *, name: str, country: str,
            website: str = "") -> Resolution:
    """
    Find the university's official site.

    Order matters. Our own catalogue first, because those URLs were verified by a human
    and recorded with provenance. An operator-supplied URL second. There is deliberately
    no third option: if we do not know the domain and nobody tells us, we stop. The
    alternative - assembling a likely domain from the name - is the single easiest way
    to make this feature start reading the wrong institution's website and reporting it
    with total confidence.
    """
    q = select(University).join(Country)
    if country.strip():
        c = _norm(country)
        q = q.where(func.lower(Country.name).contains(c) |
                    func.lower(Country.slug).contains(c))
    rows = list(db.execute(q).scalars().unique())

    target = _norm(name)
    exact = [u for u in rows if _norm(u.name) == target or u.slug == name.strip().lower()]
    partial = [u for u in rows
               if target and (target in _norm(u.name) or _norm(u.name) in target)]
    match = (exact or partial or [None])[0]

    if match is not None:
        site = (website or match.website or "").strip()
        if not site:
            return Resolution("NO_WEBSITE", match, "",
                              f"{match.name} is in the catalogue but has no website "
                              f"recorded. Supply one to continue.")
        return Resolution("RESOLVED", match, site, f"Matched catalogue entry {match.slug}.")

    if website.strip():
        return Resolution("RESOLVED", None, website.strip(),
                          "Not in the catalogue; using the website supplied by the operator.")

    near = [{"name": u.name, "slug": u.slug, "website": u.website or ""}
            for u in rows
            if target and target.split()[0] in _norm(u.name)][:5]
    return Resolution(
        "NOT_IN_CATALOGUE", None, "",
        f"No catalogue entry matches '{name}'" + (f" in {country}" if country else "") +
        ". Supply the official website URL to extract from it. This tool will not guess "
        "a domain from the name - reading the wrong institution's site is worse than "
        "returning nothing.",
        near)


# ------------------------------------------------------------------------- fetching
def strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"'),
                    ("&euro;", "€"), ("&pound;", "£"), ("&dollar;", "$"),
                    ("&lt;", "<"), ("&gt;", ">"), ("&#8217;", "'"), ("&rsquo;", "'")):
        text = text.replace(ent, ch)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def page_title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return re.sub(r"\s+", " ", strip_html(m.group(1))).strip()[:200] if m else ""


class Robots:
    """
    robots.txt, honoured. A consultancy running an automated reader against university
    sites all day is a guest on someone else's infrastructure; ignoring their stated
    preference is both rude and a good way to get the office IP banned from the sites
    the business depends on. Unreachable robots.txt is treated as permissive, which is
    the conventional reading.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._cache:
            rp: Optional[urllib.robotparser.RobotFileParser] = urllib.robotparser.RobotFileParser()
            try:
                with httpx.Client(timeout=8.0, headers=HEADERS, follow_redirects=True) as c:
                    r = c.get(urljoin(root, "/robots.txt"))
                if r.status_code == 200 and len(r.text) < 500_000:
                    rp.parse(r.text.splitlines())        # type: ignore[union-attr]
                else:
                    rp = None
            except Exception:                            # noqa: BLE001
                rp = None
            self._cache[root] = rp
        rp = self._cache[root]
        if rp is None:
            return True
        try:
            return rp.can_fetch(USER_AGENT, url)
        except Exception:                                # noqa: BLE001
            return True


@dataclass
class Page:
    url: str
    status: str
    title: str = ""
    text: str = ""
    html: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def fetch_page(url: str, robots: Optional[Robots] = None,
               client: Optional[httpx.Client] = None) -> Page:
    robots = robots or Robots()
    if not robots.allows(url):
        return Page(url, "BLOCKED_BY_ROBOTS")
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    try:
        r = client.get(url)
        if r.status_code >= 400:
            return Page(url, f"HTTP_{r.status_code}")
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return Page(url, f"SKIPPED_{ctype.split(';')[0] or 'unknown'}")
        html = r.text[:MAX_PAGE_BYTES]
        return Page(url, "OK", page_title(html), strip_html(html), html)
    except Exception as exc:                             # noqa: BLE001
        return Page(url, f"ERROR_{type(exc).__name__}")
    finally:
        if owned:
            client.close()


def links(html: str, base_url: str) -> List[Tuple[str, str]]:
    """(absolute url, anchor text) for same-host links."""
    host = urlparse(base_url).netloc
    out: List[Tuple[str, str]] = []
    seen = set()
    for m in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html or ""):
        href, anchor = m.group(1).strip(), re.sub(r"\s+", " ", strip_html(m.group(2))).strip()
        if href.lower().startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        p = urlparse(absolute)
        if p.scheme not in ("http", "https") or p.netloc != host:
            continue
        key = absolute.split("?")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append((absolute, anchor[:120]))
    return out


def score_link(url: str, anchor: str, intents: Sequence[str]) -> int:
    hay = f"{url.lower()} {anchor.lower()}"
    score = 0
    for name in intents:
        i = _intent(name)
        if not i:
            continue
        score += 2 * sum(1 for w in i.link_words if has_word(hay, w))
        score += sum(1 for w in i.page_words if has_word(hay, w))
    if any(w in hay for w in ("international", "postgraduate", "graduate", "admission")):
        score += 1
    if any(w in hay for w in ("news", "event", "alumni", "privacy", "cookie", "login",
                              "vacancy", "job", "sitemap", "archive")):
        score -= 4
    return score


# A university homepage almost never links straight to a fees table. It links to
# "Study", "Courses", "International" - and the answer is one or two clicks beyond
# that. Measured on tcd.ie: of 29 homepage links, exactly one scored above zero on a
# tuition query, and the fees page was not among them. Single-hop discovery therefore
# cannot work on real university information architecture, regardless of how good the
# scoring is. These are the signposts worth following even though they answer nothing
# themselves.
HUB_WORDS = ("study", "course", "programme", "program", "admission", "international",
             "apply", "applicant", "postgraduate", "undergraduate", "graduate",
             "prospective", "fees", "scholarship", "funding", "entry", "education",
             "academics", "future-students", "study-with-us")


def hub_score(url: str, anchor: str) -> int:
    hay = f"{url.lower()} {anchor.lower()}"
    score = sum(2 for w in HUB_WORDS if w in hay)
    if any(w in hay for w in ("news", "event", "alumni", "privacy", "cookie", "login",
                              "vacancy", "job", "sitemap", "archive", "library",
                              "visitor", "giving", "donate", "sustainability")):
        score -= 6
    depth = len([s for s in urlparse(url).path.split("/") if s])
    if depth > 4:
        score -= 2
    return score


def discover(base_url: str, intents: Sequence[str], *, max_pages: int = MAX_PAGES,
             robots: Optional[Robots] = None,
             client: Optional[httpx.Client] = None) -> List[Page]:
    """
    Breadth-first to depth 2, within a page budget.

    Level 1 follows hubs as well as direct intent matches, because the hub is how you
    reach the answer. Level 2 follows intent matches only - by then we are on the
    "Study" page and the "Fees and funding" link really is there.
    """
    robots = robots or Robots()
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    try:
        root = fetch_page(base_url, robots, client)
        pages = [root]
        if not root.ok:
            return pages
        visited = {base_url.split("?")[0].rstrip("/")}

        def take(page: Page, level: int, budget: int) -> List[Page]:
            got: List[Page] = []
            if not page.ok or budget <= 0:
                return got
            cands = []
            for url, anchor in links(page.html, page.url):
                key = url.split("?")[0].rstrip("/")
                if key in visited:
                    continue
                direct = score_link(url, anchor, intents)
                combined = direct + (hub_score(url, anchor) if level == 1 else 0)
                if combined > 0:
                    cands.append((combined, direct, url))
            cands.sort(key=lambda t: -t[0])
            for _c, _d, url in cands[:budget]:
                key = url.split("?")[0].rstrip("/")
                if key in visited:
                    continue
                visited.add(key)
                time.sleep(POLITE_DELAY)
                got.append(fetch_page(url, robots, client))
            return got

        level1 = take(root, 1, min(3, max_pages - len(pages)))
        pages.extend(level1)
        for parent in level1:
            if len(pages) >= max_pages:
                break
            pages.extend(take(parent, 2, max_pages - len(pages)))
        return pages[:max_pages]
    finally:
        if owned:
            client.close()


# ------------------------------------------------------------------------ extraction
NUMBERISH = re.compile(
    r"(?:[€£$₹¥₩]|\b(?:EUR|GBP|USD|AUD|CAD|JPY|KRW|MYR|BDT|SGD|CHF|SEK|NOK|TRY|AED|SAR)\b)"
    r"\s?\d[\d,.\s]*|\b\d+(?:[.,]\d+)?\s?(?:%|per cent|percent)|\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"
    r"|\b\d+(?:\.\d+)?\b")


def looks_like_prose(fragment: str) -> bool:
    """
    Reject keyword lists, tag clouds and meta blobs.

    Galway's course pages carry strings like "research, postgraduate, programme, course,
    nui galway, archaeology, phd, mlitt, per annum, 2014/15, inclusive of levy," which
    match the tuition vocabulary perfectly and tell a reader nothing. They are on the
    page, so the grounding check passes them quite correctly - grounding proves a quote
    is real, not that it is worth reading. That is a separate judgement and belongs
    here.

    The tell is comma density: prose averages many words between commas, a keyword list
    averages one or two.
    """
    text = fragment.strip()
    if not text or text.endswith(","):
        return False
    commas = text.count(",")
    words = len(text.split())
    if commas >= 4 and words / (commas + 1) < 3.0:
        return False
    if commas >= 2 and not re.search(r"[.!?]$", text) and words / (commas + 1) < 2.5:
        return False
    # Needs to read like a statement rather than a nav bar: several words, at least
    # one of them an ordinary lowercase word. An earlier, stricter version of this
    # demanded two adjacent lowercase words and threw away "A cumulative GPA of 3.2 or
    # better (B+ average) unweighted." - a perfectly good entry requirement - because
    # every gap happened to fall next to "GPA", "3.2" or a bracket.
    return words >= 4 and bool(re.search(r"(?<![A-Za-z])[a-z]{3,}(?![A-Za-z])", text))


def _sentences(text: str) -> List[str]:
    parts: List[str] = []
    for block in (text or "").split("\n"):
        block = block.strip()
        if not block:
            continue
        parts.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", block) if s.strip())
    return parts


def deterministic_findings(pages: Iterable[Page], intents: Sequence[str],
                           instruction: str = "") -> List[Dict[str, Any]]:
    """
    Keyword-and-pattern extraction. No model involved.

    Scores each sentence on the page by how well it matches the intent vocabulary, and
    requires a number present for intents that are fundamentally about numbers - a
    sentence about tuition that contains no figure is not an answer to "how much".
    """
    extra = [w for w in re.findall(r"[a-z]{4,}", (instruction or "").lower())
             if w not in {"what", "when", "where", "which", "about", "tell", "give",
                          "university", "please", "need", "want", "find", "does", "this",
                          "that", "from", "with", "have", "they", "their", "information"}]
    out: List[Dict[str, Any]] = []
    for page in pages:
        if not page.ok:
            continue
        for sentence in _sentences(page.text):
            if not (25 <= len(sentence) <= SNIPPET_CHARS):
                continue
            if not looks_like_prose(sentence):
                continue
            low = sentence.lower()
            for name in intents:
                i = _intent(name)
                if not i:
                    continue
                hits = sum(1 for w in i.page_words if has_word(low, w))
                if not hits:
                    continue
                has_number = bool(NUMBERISH.search(sentence))
                if i.wants_number and not has_number:
                    continue
                score = hits * 2 + (3 if has_number else 0)
                score += sum(1 for w in set(extra) if has_word(low, w))
                out.append({"intent": name, "quote": sentence, "source_url": page.url,
                            "page_title": page.title, "score": score, "method": "pattern"})
    out.sort(key=lambda f: -f["score"])
    return _dedupe(out)


def _dedupe(findings: List[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for f in findings:
        key = re.sub(r"\W+", "", f["quote"].lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
        if len(out) >= limit:
            break
    return out


def _normalise_for_grounding(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def ground(candidates: List[Dict[str, Any]], pages: Sequence[Page]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Keep only quotes that genuinely appear on a page we fetched; count the rest.

    This is the load-bearing guardrail of the whole feature. An LLM asked to quote will
    usually quote, but "usually" is not a property you can build a consultancy's advice
    on - a single smoothed-over tuition figure is a student's money. Verifying the quote
    as a substring turns the model from a source of facts into a search function over
    text we already hold, and makes fabrication impossible rather than unlikely.

    The dropped count is returned rather than hidden: a model that keeps failing this
    check is information the operator should see.
    """
    corpus = {p.url: _normalise_for_grounding(p.text) for p in pages if p.ok}
    whole = " \n ".join(corpus.values())
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for c in candidates:
        quote = _normalise_for_grounding(c.get("quote", ""))
        if len(quote) < 15:
            dropped += 1
            continue
        url = c.get("source_url", "")
        if url in corpus and quote in corpus[url]:
            kept.append(c)
        elif quote in whole:
            # Right text, wrong attribution. Repoint it rather than discard it.
            for u, body in corpus.items():
                if quote in body:
                    c["source_url"] = u
                    c["note"] = "attribution corrected to the page actually containing this text"
                    kept.append(c)
                    break
        else:
            dropped += 1
    return kept, dropped


LLM_SYSTEM = """You extract facts from university web pages for a study-abroad advisory service.

You will be given page text and a question. Return ONLY a JSON array. Each element:
  {"intent": "<one of: tuition, entry_requirements, deadlines, scholarships, programs, contact, application_process>",
   "quote": "<EXACT sentence copied character-for-character from the page text>",
   "source_url": "<the url the sentence came from>"}

Absolute rules:
- The "quote" MUST be copied verbatim from the supplied page text. Do not paraphrase,
  correct, shorten, translate, or tidy it. Copy it exactly.
- If the pages do not answer the question, return [].
- Never state a fee, score, date or requirement that is not written in the text.
Return at most 12 elements. Output nothing except the JSON array."""


def llm_findings(pages: Sequence[Page], instruction: str) -> Tuple[List[Dict[str, Any]], str]:
    usable = [p for p in pages if p.ok and p.text]
    if not usable:
        return [], "no readable pages"
    budget, blocks = 14_000, []
    for p in usable:
        chunk = p.text[: max(1200, budget // max(len(usable), 1))]
        blocks.append(f"=== SOURCE_URL: {p.url}\n{chunk}")
    prompt = (f"QUESTION: {instruction.strip() or 'Summarise the key facts for an applicant.'}\n\n"
              + "\n\n".join(blocks))
    result = llm.complete(LLM_SYSTEM, prompt, max_tokens=1400, temperature=0.0)
    if result is None:
        return [], "no LLM provider configured or reachable"
    raw = (getattr(result, "text", "") or "").strip()
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return [], "model did not return JSON"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [], "model returned malformed JSON"
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("quote"):
            out.append({"intent": str(item.get("intent") or "programs"),
                        "quote": str(item["quote"]), "source_url": str(item.get("source_url") or ""),
                        "score": 10, "method": "llm"})
    return out, ""


# -------------------------------------------------------------------------- orchestrator
def run(db: Session, *, name: str, country: str = "", instruction: str = "",
        website: str = "", max_pages: int = MAX_PAGES,
        use_llm: bool = True) -> Dict[str, Any]:
    """
    The whole job: resolve, discover, fetch, extract, ground, report.

    Returns a dict that is safe to hand straight to an API layer. Never raises on
    network trouble - a university whose site is down is a normal Tuesday, and the
    operator needs to be told that rather than shown a stack trace.
    """
    started = time.time()
    resolution = resolve(db, name=name, country=country, website=website)
    base: Dict[str, Any] = {
        "query": {"name": name, "country": country, "instruction": instruction,
                  "website": website},
        "resolution": {"status": resolution.status, "message": resolution.message,
                       "website": resolution.website,
                       "university_slug": resolution.university.slug if resolution.university else None,
                       "university_name": resolution.university.name if resolution.university else None,
                       "candidates": resolution.candidates},
        "intents": [], "pages": [], "findings": [], "dropped_ungrounded": 0,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "confidence": "UNVERIFIED",
        "notice": ("Live extraction from the institution's own website. Every quote below "
                   "was checked to appear verbatim on the page it cites. Nothing here is "
                   "verified data - a human must confirm it before it is shown to a "
                   "student or written into the catalogue."),
    }
    if resolution.status != "RESOLVED":
        base["elapsed_ms"] = int((time.time() - started) * 1000)
        return base

    intents = classify_instruction(instruction)
    base["intents"] = intents
    pages = discover(resolution.website, intents, max_pages=max_pages)
    base["pages"] = [{"url": p.url, "status": p.status, "title": p.title,
                      "chars": len(p.text)} for p in pages]

    findings: List[Dict[str, Any]] = []
    note = ""
    if use_llm:
        llm_out, note = llm_findings(pages, instruction)
        findings.extend(llm_out)
    findings.extend(deterministic_findings(pages, intents, instruction))

    grounded, dropped = ground(findings, pages)
    base["findings"] = _dedupe(grounded, limit=25)
    base["dropped_ungrounded"] = dropped
    base["llm_note"] = note
    base["method"] = ("llm+pattern" if any(f["method"] == "llm" for f in base["findings"])
                      else "pattern")
    if not base["findings"]:
        base["notice"] = ("No passage on the pages read answers this question. That is a "
                          "real answer: the information may sit behind a search form, in a "
                          "PDF, or on a page not linked from the entry point. It has not "
                          "been guessed at.")
    base["elapsed_ms"] = int((time.time() - started) * 1000)
    return base


def to_import_rows(result: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Shape findings into rows the existing importer can stage, so extracted material
    goes through the same human review, conflict detection and provenance recording as
    everything else. Extraction is a research aid, not a write path.
    """
    rows = []
    for f in result.get("findings", []):
        rows.append({
            "university": result["resolution"].get("university_slug") or "",
            "intent": f["intent"], "quote": f["quote"],
            "source_url": f["source_url"], "source_type": "TIER_1_OFFICIAL_UNIVERSITY",
            "confidence": "UNVERIFIED", "retrieved_at": result["retrieved_at"],
        })
    return rows
