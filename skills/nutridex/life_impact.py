# -*- coding: utf-8 -*-
"""life_impact.py — Life impact scoring engine for NutriDex.

Scoring methodology:
- Sums life_impact_points per matched ingredient
- Applies ultra-processing multiplier based on NOVA-4 count
- Converts to "years off your life" assuming daily consumption
- Grounded in:
  - BMJ 2019 NutriNet-Santé (Schnabel et al.): HR 1.14 per 10% UPF increase
  - JAMA 2019 (Rico-Campà et al.): HR 1.62 highest vs lowest UPF quartile
"""

from __future__ import annotations

from typing import Any


# UPF multiplier thresholds (number of NOVA-4 ingredients)
_UPF_MULTIPLIERS = [
    (13, 1.8),  # 13+ NOVA-4 items
    (9, 1.5),   # 9-12 NOVA-4 items
    (5, 1.2),   # 5-8 NOVA-4 items
    (0, 1.0),   # 0-4 NOVA-4 items
]

# Assumed remaining life expectancy for scaling (UK average at age 35)
_REMAINING_LIFE_YEARS = 47.0

# Daily servings per year
_DAYS_PER_YEAR = 365.25


def compute_life_impact(lookup_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the life impact score from additive lookup results.

    Args:
        lookup_results: Output from AdditiveDB.lookup_all()

    Returns:
        Dict with scoring breakdown and dramatic verdict.
    """
    # Tally points and NOVA-4 count
    total_points = 0.0
    nova4_count = 0
    ingredient_scores: list[dict[str, Any]] = []
    iarc_flagged: list[dict[str, Any]] = []
    categories: dict[str, int] = {}

    for result in lookup_results:
        if not result["matched"] or result["entry"] is None:
            ingredient_scores.append({
                "ingredient": result["ingredient"],
                "points": 0.0,
                "category": "unknown",
                "nova_class": None,
                "note": "Not in database — assumed benign",
            })
            continue

        entry = result["entry"]
        points = entry.get("life_impact_points", 0.0)
        nova = entry.get("nova_class", 1)
        cat = entry.get("category", "unknown")

        total_points += points
        if nova == 4:
            nova4_count += 1
        categories[cat] = categories.get(cat, 0) + 1

        score_entry = {
            "ingredient": result["ingredient"],
            "matched_as": entry.get("_matched_name", ""),
            "e_number": entry.get("id", ""),
            "points": points,
            "category": cat,
            "nova_class": nova,
            "health_effects": entry.get("health_effects", ""),
            "fun_fact": entry.get("fun_fact", ""),
        }
        ingredient_scores.append(score_entry)

        if entry.get("iarc_group"):
            iarc_flagged.append({
                "ingredient": result["ingredient"],
                "e_number": entry.get("id", ""),
                "iarc_group": entry["iarc_group"],
                "health_effects": entry.get("health_effects", ""),
            })

    # Apply UPF multiplier
    upf_multiplier = 1.0
    for threshold, mult in _UPF_MULTIPLIERS:
        if nova4_count >= threshold:
            upf_multiplier = mult
            break

    adjusted_points = total_points * upf_multiplier

    # Convert to "years off your life" (daily consumption model)
    # Methodology: points represent a fractional annual hazard increase
    # Scaled to remaining life expectancy
    years_off_central = adjusted_points * _REMAINING_LIFE_YEARS / 100.0
    years_off_optimistic = years_off_central * 0.5
    years_off_pessimistic = years_off_central * 2.0

    # Dramatic verdict
    if years_off_central < 0.01:
        verdict = "SAINT"
        verdict_text = "This is basically health food. Your body temple remains undefiled."
        emoji = "😇"
        colour = "green"
    elif years_off_central < 0.05:
        verdict = "MOSTLY HARMLESS"
        verdict_text = "Like a paper cut on your lifespan. You'll barely notice."
        emoji = "🟢"
        colour = "green"
    elif years_off_central < 0.15:
        verdict = "PROCEED WITH CAUTION"
        verdict_text = "Not great, not terrible. The Chernobyl of snacks."
        emoji = "🟡"
        colour = "yellow"
    elif years_off_central < 0.30:
        verdict = "DIETARY DANGER ZONE"
        verdict_text = "Your arteries just filed a formal complaint."
        emoji = "🟠"
        colour = "orange"
    elif years_off_central < 0.50:
        verdict = "CHEMICAL WARFARE"
        verdict_text = "This ingredient list reads like a chemistry exam you're about to fail."
        emoji = "🔴"
        colour = "red"
    else:
        verdict = "EXTINCTION-LEVEL EVENT"
        verdict_text = "Congratulations, you've found the dietary equivalent of a meteor strike."
        emoji = "💀"
        colour = "red"

    return {
        "total_raw_points": round(total_points, 4),
        "nova4_count": nova4_count,
        "total_ingredients": len(lookup_results),
        "matched_ingredients": sum(1 for r in lookup_results if r["matched"]),
        "unmatched_ingredients": sum(1 for r in lookup_results if not r["matched"]),
        "upf_multiplier": upf_multiplier,
        "adjusted_points": round(adjusted_points, 4),
        "years_off_central": round(years_off_central, 4),
        "years_off_optimistic": round(years_off_optimistic, 4),
        "years_off_pessimistic": round(years_off_pessimistic, 4),
        "verdict": verdict,
        "verdict_text": verdict_text,
        "verdict_emoji": emoji,
        "verdict_colour": colour,
        "ingredient_scores": ingredient_scores,
        "iarc_flagged": iarc_flagged,
        "category_breakdown": categories,
    }
