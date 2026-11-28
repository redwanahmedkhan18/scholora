"""W3 - validate factual claims against the evidence the answer is actually built from.

Citation presence is not factual support. An answer can carry a perfectly good source URL and
still state a fee that appears nowhere in the retrieved passages. This module therefore checks
the *claims* in the generated text against the verified evidence that was available for that
turn:

* numbers (money, percentages, English/CGPA scores, dates and other figures) must occur in the
  evidence, in the same category, from a record that passed W10;
* a named entity must be present in the evidence, and a categorical claim ("MOI is accepted",
  "the scholarship is fully funded") must be supported by those words, not inferred;
* demonstration passages and unverified records can never support a claim;
* when evidence for the same entity disagrees, a claim citing either value is withheld rather
  than presented as settled;
* sentences that are advice, questions or house notices are left alone - the validator removes
  unsupported *facts*, it does not silence the assistant.

The validator is deliberately conservative and deterministic. It is not a second verification
policy: W10 decides which records may be evidence at all; W3 only asks whether this sentence's
facts are in that evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ------------------------------------------------------------------------- evidence
_DEMO = {"DEMO", "SEED", "SYNTHETIC"}

_CURRENCY = (r"(?:[$€£₹¥₩]|\b(?:USD|GBP|EUR|AUD|CAD|NZD|JPY|KRW|MYR|HUF|AED|SAR|BDT|CHF|"
             r"SEK|NOK|TRY|SGD)\b)")
_MONEY_RE = re.compile(rf"{_CURRENCY}\s?([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
_PERCENT_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s?(?:%|per\s?cent|percent)", re.I)
_SCORE_LABEL_RE = re.compile(
    r"(IELTS|TOEFL|PTE|CGPA|GPA|DUOLINGO|SAT|GRE|GMAT)\b[^0-9]{0,18}([0-9]+(?:\.[0-9]+)?)", re.I)
_SCORE_NUM_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)[^A-Za-z0-9]{0,12}(IELTS|TOEFL|PTE|CGPA|GPA|band)", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_DATE_RE = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+20\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d{2}"
    r"|20\d{2}-\d{2}-\d{2})\b", re.I)
_NUM_RE = re.compile(r"(?<![\w.])([0-9][0-9,]*(?:\.[0-9]+)?)(?![\w])")

_MONEY_KEYS = ("tuition", "fee", "cost", "amount", "stipend", "living", "budget", "deposit",
               "insurance", "percentage_of")
_SCORE_KEYS = ("cgpa", "ielts", "toefl", "pte", "duolingo", "gre", "gmat", "min_", "band")
_DATE_KEYS = ("year", "deadline", "class_start", "application_", "intake", "date")
_ENTITY_KEYS = ("university", "program", "scholarship", "name", "title", "provider",
                "label", "country", "department")

_ASSERTION_RE = re.compile(
    r"\b(is|are|was|were|offers?|offered|requires?|required|accepts?|accepted|costs?|"
    r"charges?|covers?|covered|has|have|includes?|located|founded|established|deadline|"
    r"ranked|tuition|fees?|living|scholarship|minimum|eligibility|intake|visa)\b", re.I)

_ADVICE_RE = re.compile(
    r"^(you (can|could|should|may|might)|consider|we recommend|please |book |contact |message |"
    r"reply |ask (a|the) counselor|would you|search the|check the|visit the|compare |"
    r"a counselor can|if you (want|need|have)|let me know|tell me|here is what|here are)",
    re.I)
_NOTICE_RE = re.compile(
    r"^(every figure above|eligibility statements|contact details are|answers are built|"
    r"nothing has been guessed|i don't have a verified|verified records are currently|"
    r"i can't answer that|scholara's assistant|this needs a counselor|some details were withheld)",
    re.I)

_CATEGORICAL = (
    ("moi", ("moi", "medium of instruction")),
    ("scholarship", ("scholarship", "funding", "funded", "bursary", "waiver", "stipend")),
    ("deadline", ("deadline", "closing date", "applications open", "apply by")),
    ("accreditation", ("accredit", "recognised", "recognized")),
    ("accepting", ("accepting phd", "accepting students", "supervisor")),
    ("visa", ("visa", "work rights", "post-study")),
    ("ranking", ("rank", "ranking")),
)
_CATEGORY_TRIGGER = {
    "moi": re.compile(r"\bmoi\b|medium of instruction", re.I),
    "scholarship": re.compile(r"\bscholarship|\bfunding\b|\bfunded\b|\bbursary\b|\bwaiver\b|\bstipend\b", re.I),
    "deadline": re.compile(r"\bdeadline\b|closing date|applications? open|apply by", re.I),
    "accreditation": re.compile(r"\baccredit|\brecogni[sz]ed\b", re.I),
    "accepting": re.compile(r"\baccepting\b|phd supervisor", re.I),
    "visa": re.compile(r"\bvisa\b|work rights|post-study", re.I),
    "ranking": re.compile(r"\brank(ed|ing)?\b", re.I),
}


@dataclass
class EvidenceItem:
    text: str
    source_url: Optional[str]
    label: str
    verified: bool = True
    provenance: Optional[str] = None

    def usable(self) -> bool:
        if not self.verified:
            return False
        if (self.provenance or "").upper() in _DEMO:
            return False
        return bool(self.text.strip())


@dataclass
class EvidenceIndex:
    items: List[EvidenceItem] = field(default_factory=list)
    money: Set[str] = field(default_factory=set)
    percent: Set[str] = field(default_factory=set)
    score: Set[str] = field(default_factory=set)
    date: Set[str] = field(default_factory=set)
    number: Set[str] = field(default_factory=set)
    entities: Set[str] = field(default_factory=set)
    text: str = ""

    def all_text(self) -> str:
        return self.text


def _norm_number(value: str) -> str:
    cleaned = value.replace(",", "").strip()
    try:
        number = float(cleaned)
    except ValueError:
        return cleaned.lower()
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _scan_numbers(text: str, index: EvidenceIndex) -> None:
    for match in _MONEY_RE.finditer(text):
        index.money.add(_norm_number(match.group(1)))
    for match in _PERCENT_RE.finditer(text):
        index.percent.add(_norm_number(match.group(1)))
    for match in _SCORE_LABEL_RE.finditer(text):
        index.score.add(_norm_number(match.group(2)))
    for match in _SCORE_NUM_RE.finditer(text):
        index.score.add(_norm_number(match.group(1)))
    for match in _YEAR_RE.finditer(text):
        index.date.add(_norm_number(match.group(1)))
        index.number.add(_norm_number(match.group(1)))
    for match in _DATE_RE.finditer(text):
        index.date.add(re.sub(r"\s+", " ", match.group(1)).strip().lower())
    for match in _NUM_RE.finditer(text):
        index.number.add(_norm_number(match.group(1)))


def _flatten(value: Any, *, key: str = "", out: List[str]) -> None:
    if isinstance(value, dict):
        label = " ".join(str(value.get(k) or "") for k in _ENTITY_KEYS if value.get(k))
        if value.get("verified") is False:
            return
        if (value.get("provenance") or "").upper() in _DEMO:
            return
        if label:
            out.append(label)
        for child_key, child in value.items():
            if child_key in ("verified", "verification_reason", "verification_note",
                             "policy_version", "academic_year"):
                continue
            _flatten(child, key=str(child_key), out=out)
    elif isinstance(value, list):
        for child in value:
            _flatten(child, key=key, out=out)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        if str(value).strip():
            out.append(f"{key}: {value}" if key else str(value))


def _typed_from_structured(value: Any, index: EvidenceIndex) -> None:
    """Type the numbers in structured tool rows by the field they came from.

    A JSON row says ``tuition_annual: 21234.0`` with no currency symbol; without this the
    template's "USD 21,234" would look unsupported even though it is copied from the record.
    """
    if isinstance(value, dict):
        if value.get("verified") is False or (value.get("provenance") or "").upper() in _DEMO:
            return
        for key, child in value.items():
            k = str(key).lower()
            if isinstance(child, (int, float)) and not isinstance(child, bool):
                number = _norm_number(str(child))
                index.number.add(number)
                if any(marker in k for marker in _MONEY_KEYS):
                    index.money.add(number)
                if any(marker in k for marker in _SCORE_KEYS):
                    index.score.add(number)
                if "percent" in k or "percentage" in k:
                    index.percent.add(number)
                if any(marker in k for marker in _DATE_KEYS):
                    index.date.add(number)
            else:
                _typed_from_structured(child, index)
    elif isinstance(value, list):
        for child in value:
            _typed_from_structured(child, index)


def build_index(hits: Optional[Sequence[Dict[str, Any]]] = None,
                data: Optional[Dict[str, Any]] = None) -> EvidenceIndex:
    """Collect only verified evidence, with its numbers and entity names."""
    index = EvidenceIndex()
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        item = EvidenceItem(
            text=f"{hit.get('title', '')} {hit.get('content', '')}".strip(),
            source_url=hit.get("source_url") or hit.get("url_path"),
            label=str(hit.get("title") or ""),
            verified=bool(hit.get("verified", False)),
            provenance=hit.get("provenance"),
        )
        if item.usable():
            index.items.append(item)
            if item.label:
                index.entities.add(item.label.strip().lower())

    plain: List[str] = []
    _flatten(data or {}, out=plain)
    if plain:
        index.items.append(EvidenceItem(text="\n".join(plain), source_url=None,
                                        label="structured records", verified=True))
        for token in re.findall(r"[A-Za-z][A-Za-z&.'-]{3,}", " ".join(plain)):
            if token.lower() not in {"from", "with", "that", "this", "their", "year", "tuition",
                                     "program", "programme", "university", "country", "deadline",
                                     "minimum", "currency", "source", "verified", "available"}:
                index.entities.add(token.lower())
    _typed_from_structured(data or {}, index)

    for item in index.items:
        _scan_numbers(item.text, index)
    index.text = " \n ".join(item.text for item in index.items)
    return index


# -------------------------------------------------------------------------- claims
@dataclass
class ClaimCheck:
    sentence: str
    supported: bool
    reason: str = ""


@dataclass
class ValidationResult:
    text: str
    status: str
    checked: int = 0
    rejected: List[ClaimCheck] = field(default_factory=list)
    rejected_count: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "claims_checked": self.checked,
            "claims_rejected": self.rejected_count,
            "reasons": [r.reason for r in self.rejected],
        }


def _sentences(text: str) -> List[str]:
    out: List[str] = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        # Keep bullet text but split on sentence end. A figure is usually inside one sentence.
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line) if s.strip())
    return out


def _claim_numbers(sentence: str) -> List[Tuple[str, str]]:
    """(category, normalised value) for every figure the sentence asserts."""
    out: List[Tuple[str, str]] = []
    for match in _MONEY_RE.finditer(sentence):
        out.append(("money", _norm_number(match.group(1))))
    for match in _PERCENT_RE.finditer(sentence):
        out.append(("percent", _norm_number(match.group(1))))
    for match in _SCORE_LABEL_RE.finditer(sentence):
        out.append(("score", _norm_number(match.group(2))))
    for match in _SCORE_NUM_RE.finditer(sentence):
        out.append(("score", _norm_number(match.group(1))))
    for match in _DATE_RE.finditer(sentence):
        out.append(("date", re.sub(r"\s+", " ", match.group(1)).strip().lower()))
    consumed = {value for _, value in out}
    for match in _YEAR_RE.finditer(sentence):
        value = _norm_number(match.group(1))
        if value not in consumed:
            out.append(("date", value))
    for match in _NUM_RE.finditer(sentence):
        value = _norm_number(match.group(1))
        if value not in {v for _, v in out}:
            out.append(("number", value))
    return out


def _is_factual(sentence: str, index: EvidenceIndex) -> bool:
    if sentence.endswith("?"):
        return False
    if _ADVICE_RE.search(sentence) or _NOTICE_RE.search(sentence):
        return False
    if _claim_numbers(sentence):
        return True
    if any(trigger.search(sentence) for trigger in _CATEGORY_TRIGGER.values()):
        return True
    if len(sentence.split()) < 4:
        return False
    low = sentence.lower()
    if any(entity in low for entity in index.entities) and _ASSERTION_RE.search(sentence):
        return True
    return False


def _entity_conflicts(index: EvidenceIndex) -> Set[str]:
    """Entities for which the evidence itself holds more than one distinct money figure."""
    values: Dict[str, Set[str]] = {}
    for item in index.items:
        if not item.label or item.label == "structured records":
            continue
        found = {_norm_number(m.group(1)) for m in _MONEY_RE.finditer(item.text)}
        if found:
            values.setdefault(item.label.lower(), set()).update(found)
    return {label for label, money in values.items() if len(money) > 1}


def _supported(sentence: str, index: EvidenceIndex, conflicts: Set[str]) -> ClaimCheck:
    low = sentence.lower()
    named = [entity for entity in index.entities if len(entity) > 2 and entity in low]

    numbers = _claim_numbers(sentence)
    for category, value in numbers:
        bucket = getattr(index, category, index.number)
        if value in bucket:
            continue
        # A figure written identically but typed differently (e.g. a year inside a fee context)
        # is still a figure we cannot attribute to the evidence category it claims.
        return ClaimCheck(sentence, False, f"UNSUPPORTED_NUMBER:{category}:{value}")

    if named:
        money_claim = any(category == "money" for category, _ in numbers)
        if money_claim:
            for entity in named:
                if entity in conflicts:
                    return ClaimCheck(sentence, False, f"CONFLICTING_EVIDENCE:{entity}")
        if not any(entity in index.text.lower() for entity in named):
            return ClaimCheck(sentence, False, "UNSUPPORTED_ENTITY")

    for category, words in _CATEGORICAL:
        if not _CATEGORY_TRIGGER[category].search(sentence):
            continue
        if not any(word in index.text.lower() for word in words):
            return ClaimCheck(sentence, False, f"UNSUPPORTED_CLAIM:{category}")

    return ClaimCheck(sentence, True)


def validate(answer: str, hits: Optional[Sequence[Dict[str, Any]]] = None,
             data: Optional[Dict[str, Any]] = None) -> ValidationResult:
    """Validate the factual sentences of an answer against the evidence for this turn."""
    index = build_index(hits, data)
    sentences = _sentences(answer)
    if not index.items:
        # No evidence at all. Keep non-factual house text only; a factual sentence cannot be
        # supported by nothing.
        kept: List[str] = []
        rejected: List[ClaimCheck] = []
        checked = 0
        for sentence in sentences:
            if _claim_numbers(sentence) or (index.entities and _ASSERTION_RE.search(sentence)
                                            and any(e in sentence.lower() for e in index.entities)):
                checked += 1
                rejected.append(ClaimCheck(sentence, False, "NO_EVIDENCE"))
            else:
                kept.append(sentence)
        status = "REJECTED" if rejected else "NO_CLAIMS"
        return ValidationResult("\n".join(kept).strip(), status, checked, rejected, len(rejected))

    conflicts = _entity_conflicts(index)
    kept_claims: List[str] = []
    rejected_claims: List[ClaimCheck] = []
    checked = 0
    for sentence in sentences:
        if not _is_factual(sentence, index):
            kept_claims.append(sentence)
            continue
        checked += 1
        verdict = _supported(sentence, index, conflicts)
        if verdict.supported:
            kept_claims.append(sentence)
        else:
            rejected_claims.append(verdict)

    if checked == 0:
        status = "NO_CLAIMS"
    else:
        status = "VERIFIED" if not rejected_claims else (
            "QUALIFIED" if kept_claims else "REJECTED")
    text = "\n".join(kept_claims).strip()
    return ValidationResult(text, status, checked, rejected_claims, len(rejected_claims))
