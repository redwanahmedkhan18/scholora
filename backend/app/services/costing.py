"""Tuition + personalized cost engine (§30/§31)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.models.education import Program
from app.services import fx


def estimate(program: Program, budget: Optional[float] = None, budget_currency: str = "USD") -> Dict[str, Any]:
    tuition = program.tuition_annual or 0.0
    living = program.living_cost_annual or (program.university.country.living_cost_min if program.university and program.university.country else 0) or 0.0
    insurance = program.insurance_annual or 0.0
    application_fee = program.application_fee or 0.0
    other = sum(float(f.get("amount", 0) or 0) for f in (program.other_fees or []))
    first_year_total = tuition + living + insurance + other
    breakdown = [
        {"label": "Annual tuition", "amount": tuition, "included_in_total": True},
        {"label": "Estimated living cost (per year)", "amount": living, "included_in_total": True},
        {"label": "Health insurance (per year)", "amount": insurance, "included_in_total": True},
        *[{"label": f.get("label", "Other fee"), "amount": float(f.get("amount", 0) or 0), "included_in_total": True}
          for f in (program.other_fees or [])],
        {"label": "Application fee (one-time)", "amount": application_fee, "included_in_total": False},
        {"label": "Tuition deposit (one-time, adjustable)", "amount": program.deposit or 0.0, "included_in_total": False},
    ]
    result: Dict[str, Any] = {
        "currency": program.currency or "USD",
        "breakdown": breakdown,
        "estimated_annual_total": round(first_year_total, 2),
        "one_time_costs": round(application_fee + (program.deposit or 0.0), 2),
        "budget_status": "NOT_PROVIDED",
        "budget_gap": None,
        "note": "Figures are indicative estimates from published university sources and are subject to change.",
    }
    if budget:
        programme_currency = result["currency"]
        stated_currency = (budget_currency or programme_currency).upper()
        result["budget_currency"] = stated_currency
        result["stated_budget"] = round(float(budget), 2)

        # The two figures must be in the same currency before they can be subtracted. They
        # frequently are not: most programmes here are priced in GBP, EUR, CAD, AUD or NZD
        # while Bangladeshi students usually state a budget in USD or BDT.
        budget_in_programme_currency = fx.convert(float(budget), stated_currency, programme_currency)

        if budget_in_programme_currency is None:
            # Unknown currency pair. Report both figures and decline to judge, rather than
            # subtracting two numbers that do not share a unit.
            result["budget_status"] = "BUDGET_CURRENCY_MISMATCH"
            result["budget_gap"] = None
            result["budget_note"] = (
                f"Your budget is stated in {stated_currency} and this programme is priced in "
                f"{programme_currency}. No conversion rate is on file, so the two cannot be "
                f"compared here. Your counselor can confirm the current rate."
            )
            return result

        if stated_currency != programme_currency:
            result["budget_conversion"] = fx.provenance(stated_currency, programme_currency)
            result["budget_in_programme_currency"] = round(budget_in_programme_currency, 2)

        gap = round(first_year_total - budget_in_programme_currency, 2)
        if gap <= 0:
            result["budget_status"] = "WITHIN_STATED_BUDGET"
        elif gap <= budget_in_programme_currency * 0.15:
            result["budget_status"] = "SLIGHTLY_ABOVE_BUDGET"
        else:
            result["budget_status"] = "ABOVE_STATED_BUDGET"
        result["budget_gap"] = gap
    return result
