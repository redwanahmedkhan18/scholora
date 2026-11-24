"""Indicative currency conversion for budget comparison only.

Why this module exists: 35 of the 55 seeded programmes are priced in something other than USD.
The cost engine used to compute `programme_total - student_budget` directly, with no conversion
and with the `budget_currency` argument accepted but ignored, so a GBP 28,350/year programme was
reported as WITHIN_STATED_BUDGET against a USD 35,000 budget - when GBP 28,350 is roughly
USD 36,000, i.e. over it. That is a wrong financial statement shown to a student, which is the
exact failure mode the trust policy exists to prevent.

Two rules, both of which follow from that policy:

1. **A rate is data, so it carries provenance.** Every rate below records where it came from and
   when, and the API returns that alongside any converted figure. Rates are described as
   indicative and are never presented as a live quote.
2. **No rate means no verdict.** If a pair cannot be converted the engine returns
   BUDGET_CURRENCY_MISMATCH and shows both figures unconverted, rather than comparing two
   incomparable numbers. Saying "I cannot compare these" is always better than a confident
   wrong answer.

These are static mid-market approximations for the currencies in the dataset. They are good
enough to decide "comfortably within budget" vs "clearly over", which is all the cost engine
claims. They are not good enough to quote a fee, and nothing in the product does.

To refresh: update RATES and RATES_AS_OF together, in one commit.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Units of the given currency per 1 USD.
RATES: Dict[str, float] = {
    "USD": 1.00,
    "EUR": 0.92,
    "GBP": 0.79,
    "CAD": 1.36,
    "AUD": 1.52,
    "NZD": 1.64,
    "BDT": 110.0,
    "INR": 83.0,
    "JPY": 157.0,
    "KRW": 1360.0,
    "MYR": 4.70,
    "AED": 3.67,
    "CHF": 0.89,
    "SEK": 10.60,
    "HUF": 360.0,
}

RATES_AS_OF = "2026-01-01"
RATES_SOURCE = "Indicative mid-market reference rates, reviewed manually."
RATES_NOTE = (
    "Converted using an indicative reference rate, not a live quote. "
    "Confirm the actual amount with your bank before transferring any fee."
)


def supported(currency: Optional[str]) -> bool:
    return bool(currency) and currency.upper() in RATES


def convert(amount: float, source: str, target: str) -> Optional[float]:
    """Convert between two known currencies. Returns None when either side is unknown."""
    if amount is None:
        return None
    source, target = (source or "").upper(), (target or "").upper()
    if source == target:
        return float(amount)
    if source not in RATES or target not in RATES:
        return None
    usd = float(amount) / RATES[source]
    return usd * RATES[target]


def provenance(source: str, target: str) -> Dict[str, Any]:
    """Provenance block for a conversion, matching the shape used elsewhere in the API."""
    return {
        "converted": True,
        "from_currency": (source or "").upper(),
        "to_currency": (target or "").upper(),
        "rate_as_of": RATES_AS_OF,
        "source_type": "INDICATIVE_REFERENCE_RATE",
        "source": RATES_SOURCE,
        "note": RATES_NOTE,
    }
