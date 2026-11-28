"""Academic-year extraction and matching.

A student asking about "the 2026 intake" or "2026/27" is asking about a *specific* point in
time. Presenting a 2025 fee or a 2028 deadline as the answer is exactly the kind of
confidently-wrong fact this platform exists to prevent, so the question's year is extracted
deterministically and carried all the way to the read-time verification policy.

Supported forms (case-insensitive):
    2026 / 2026 intake / Fall 2026 / September 2026 / academic year 2026 / fees for 2026
    2026/27 / 2026-27 / 2026/2027 / 2026–2027

A bare four-digit number is only treated as a year when it looks like one in context
(an intake/entry/start word nearby, a range, or "for/in/of/during 2026"). "IELTS 6.5 or a
3.2 CGPA" must not become a year constraint.

Ambiguity is handled by matching any named year: "2026 or 2027" means either is acceptable.
No year in the question means no constraint; the caller must not invent one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional, Set

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
)
_TERMS = ("fall", "autumn", "spring", "summer", "winter", "intake", "entry", "session",
          "academic year", "commencing", "starting", "start", "beginning")
_TERM_RE = "|".join(_TERMS)
_MONTH_RE = "|".join(_MONTHS)

# 2026/27, 2026-27, 2026/2027, 2026–2027. The en/em dashes are normalised first.
_RANGE_RE = re.compile(r"\b(20\d{2})\s*[/\-]\s*(20)?(\d{2})\b")

# A set of years joined by a conjunction: "2026 or 2027", "2026 and 2027".
_OR_YEARS_RE = re.compile(r"\b(20\d{2})\s*(?:or|and|,)\s*(20\d{2})\b", re.I)

# A term/month followed by a year: "Fall 2026", "September 2026 intake", "academic year 2026".
_TERM_YEAR_RE = re.compile(
    rf"\b(?:{_TERM_RE}|{_MONTH_RE})\b[\s,:.-]{{0,24}}(20\d{{2}})\b", re.I)
# A year followed by a term: "2026 intake", "2026/27 entry", "2026 academic year".
_YEAR_TERM_RE = re.compile(
    rf"\b(20\d{{2}})\b[\s,:.-]{{0,12}}(?:{_TERM_RE}|{_MONTH_RE})\b", re.I)
# "for/in/of/during/by 2026": a year used as a target year, not a score.
_PREPOSITION_YEAR_RE = re.compile(
    r"\b(?:for|in|of|during|by|from|until|till|before|after|starting|beginning)\s+(?:the\s+)?"
    r"(20\d{2})\b", re.I)


@dataclass(frozen=True)
class AcademicYear:
    """A normalised academic-year reference.

    ``years`` holds every calendar year the reference can legitimately mean. A range such as
    2026/27 covers both the autumn-2026 and spring-2027 intakes, so matching is by
    intersection rather than by string equality.
    """

    label: str
    years: frozenset[int]
    #: Start years of *academic-year ranges* the reference names (2026 for "2026/27").
    #: ``years`` alone cannot distinguish "2026/27" from "2027/28": both contain 2027, but
    #: they are different academic years and must not satisfy each other. A plain intake year
    #: ("Fall 2026") leaves this empty, so it still matches by calendar-year intersection.
    ay_starts: frozenset[int] = frozenset()

    def matches(self, other_years: Iterable[int]) -> bool:
        return bool(self.years & {int(y) for y in other_years if y is not None})

    def as_dict(self) -> dict:
        return {"label": self.label, "years": sorted(self.years)}


def _iso_years(value: Any) -> Set[int]:
    """Years a stored record can be about, from a year int, an AY string, or a date."""
    out: Set[int] = set()
    if value is None:
        return out
    if isinstance(value, bool):
        return out
    if isinstance(value, int):
        return {value} if 1900 <= value <= 2200 else set()
    if isinstance(value, (datetime, date)):
        return {value.year}
    text = str(value).strip()
    if not text:
        return out
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        return {year} if 1900 <= year <= 2200 else set()
    parsed = parse(text)
    return set(parsed.years) if parsed else out


def parse(text: str) -> Optional[AcademicYear]:
    """Extract the academic year a question is about, or ``None`` when it names none."""
    raw = (text or "").strip()
    if not raw:
        return None
    normalised = (raw.replace("\u2013", "-").replace("\u2014", "-")
                  .replace("\u2212", "-").replace("\u00a0", " "))

    years: Set[int] = set()
    ay_starts: Set[int] = set()
    labels: list[str] = []

    for match in _RANGE_RE.finditer(normalised):
        start = int(match.group(1))
        if start < 1900 or start > 2200:
            continue
        suffix = match.group(3)
        end = int((match.group(2) or str(start)[:2]) + suffix)
        if end < start or end > start + 1:
            continue
        years.update((start, end))
        ay_starts.add(start)
        labels.append(f"{start}/{str(end)[-2:]}")

    # Remove consumed ranges so their years are not counted twice by the single-year rules.
    without_ranges = _RANGE_RE.sub(" ", normalised)
    for match in _OR_YEARS_RE.finditer(without_ranges):
        for group in (1, 2):
            year = int(match.group(group))
            if 1900 <= year <= 2200:
                years.add(year)
                labels.append(str(year))
    without_ranges = _OR_YEARS_RE.sub(" ", without_ranges)
    for pattern in (_TERM_YEAR_RE, _YEAR_TERM_RE, _PREPOSITION_YEAR_RE):
        for match in pattern.finditer(without_ranges):
            year = int(match.group(1))
            if 1900 <= year <= 2200:
                years.add(year)
                labels.append(str(year))

    if not years and re.fullmatch(r"20\d{2}", normalised):
        year = int(normalised)
        if 1900 <= year <= 2200:
            years.add(year)
            labels.append(str(year))

    if not years:
        return None
    # De-duplicate while preserving the first wording for a stable label.
    seen: list[str] = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    return AcademicYear(label=" or ".join(seen), years=frozenset(years),
                        ay_starts=frozenset(ay_starts))


def _range_starts(value: Any) -> Set[int]:
    """Academic-year range starts named by one stored value (e.g. "2026/27" -> {2026})."""
    if value is None or isinstance(value, (bool, int, datetime, date)):
        return set()
    text = str(value).strip()
    if not text:
        return set()
    normalised = (text.replace("\u2013", "-").replace("\u2014", "-")
                  .replace("\u2212", "-").replace("\u00a0", " "))
    out: Set[int] = set()
    for match in _RANGE_RE.finditer(normalised):
        start = int(match.group(1))
        suffix = match.group(3)
        end = int((match.group(2) or str(start)[:2]) + suffix)
        if 1900 <= start <= 2200 and start <= end <= start + 1:
            out.add(start)
    return out


def record_ay_starts(record: Any) -> Set[int]:
    """Academic-year range starts a record names, from the same fields ``record_years`` reads."""
    if isinstance(record, dict):
        get = record.get
        intakes = record.get("intakes") or []
    else:
        get = lambda name, default=None: getattr(record, name, default)  # noqa: E731
        intakes = getattr(record, "intakes", None) or []

    out: Set[int] = set()
    for field in ("academic_year", "year", "label"):
        value = get(field) if callable(get) else None
        out |= _range_starts(value)
    for intake in intakes:
        out |= record_ay_starts(intake)
    return out


def record_years(record: Any) -> Set[int]:
    """Every academic year a record can be about, using only fields the record really has.

    Intake rows carry ``year``, a label ("Fall 2026") and dates. A programme has no year of
    its own; its intakes do, and the caller usually has them loaded. When nothing on the
    record names a year the set is empty, which the policy reports as YEAR_UNKNOWN rather
    than stretching a figure to fit.
    """
    if isinstance(record, dict):
        get = record.get
        intakes = record.get("intakes") or []
    else:
        get = lambda name, default=None: getattr(record, name, default)  # noqa: E731
        intakes = getattr(record, "intakes", None) or []

    out: Set[int] = set()
    for field in ("academic_year", "year", "label"):
        value = get(field) if callable(get) else None
        out |= _iso_years(value)
    for field in ("class_start", "application_deadline", "application_opens"):
        out |= _iso_years(get(field) if callable(get) else None)
    for intake in intakes:
        out |= record_years(intake)
    return out


def same_academic_year(requested: "AcademicYear", record: Any) -> bool:
    """Whether a record can answer a question that named this academic year.

    Two named academic-year ranges must share their *start* year. "2026/27" and "2027/28"
    both cover calendar 2027, but they are different academic years, so calendar intersection
    alone would wrongly let one answer the other. When either side names only a plain intake
    year (no range), the calendar-year intersection is the correct and more permissive rule.
    """
    recorded_years = record_years(record)
    if not recorded_years:
        return False
    recorded_starts = record_ay_starts(record)
    if requested.ay_starts and recorded_starts:
        return bool(requested.ay_starts & recorded_starts)
    return requested.matches(recorded_years)
