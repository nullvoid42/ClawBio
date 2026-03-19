#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nutridex.py — NutriDex: Additive Analysis & Life Impact Scoring
ClawBio Skill v0.1.0

Analyses food ingredients and E-numbers, looks up health effects, and delivers
a tongue-in-cheek "years off your life" score.

Usage:
    python nutridex.py --ingredients "salt, sugar, E150d, maltodextrin" --output /tmp/nutridex
    python nutridex.py --input ingredients.json --product walkers_cheese_onion --output /tmp/nutridex
    python nutridex.py --demo --output /tmp/nutridex_demo
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Add project root to path for shared imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from clawbio.common.report import write_result_json, DISCLAIMER

# Local imports (same directory)
_SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SKILL_DIR))

from additive_db import AdditiveDB
from life_impact import compute_life_impact
from generate_report import generate_report


def _parse_ingredients_string(raw: str) -> list[str]:
    """Parse a comma-separated or newline-separated ingredients string.

    Handles nested parenthetical sub-ingredients like:
      "Seasoning [Salt, Sugar, Flavour Enhancer (MSG)]"
    """
    # Replace brackets with commas to flatten nested ingredients
    flattened = re.sub(r"[\[\]()]", ",", raw)
    # Split on commas
    parts = [p.strip() for p in flattened.split(",")]
    # Filter empties and pure percentages
    ingredients = []
    for p in parts:
        p = p.strip().strip(".")
        if not p:
            continue
        # Skip pure numbers/percentages like "56%", "0.08%"
        if re.match(r"^[\d.]+%?$", p):
            continue
        # Skip very short tokens that are likely noise
        if len(p) < 2:
            continue
        ingredients.append(p)
    return ingredients


def main():
    parser = argparse.ArgumentParser(
        description="NutriDex — additive analysis and life impact scoring"
    )
    parser.add_argument(
        "--ingredients",
        help="Comma-separated ingredient list (from label or vision extraction)"
    )
    parser.add_argument(
        "--product",
        help="Product key from demo_ingredients.json (e.g., walkers_cheese_onion)"
    )
    parser.add_argument(
        "--input",
        help="Path to JSON file with ingredient data"
    )
    parser.add_argument(
        "--output", default="nutridex_report",
        help="Output directory (created if absent)"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run with built-in demo data (Walkers Cheese & Onion)"
    )
    parser.add_argument(
        "--no-figures", action="store_true",
        help="Skip figure generation"
    )
    args = parser.parse_args()

    # ── Resolve input ─────────────────────────────────────────────────────────
    product_name = "Unknown Product"
    ingredients: list[str] = []

    if args.demo:
        print("[NutriDex] Running in demo mode with Walkers Cheese & Onion\n")
        demo_path = _SKILL_DIR / "data" / "demo_ingredients.json"
        with open(demo_path, encoding="utf-8") as f:
            demo_data = json.load(f)
        product_key = args.product or demo_data.get("default_demo", "walkers_cheese_onion")
        product = demo_data["products"][product_key]
        product_name = product["product_name"]
        ingredients = product["ingredients_parsed"]
    elif args.ingredients:
        ingredients = _parse_ingredients_string(args.ingredients)
        product_name = args.product or "User-Provided Product"
    elif args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
            sys.exit(1)
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)
        # Support both direct ingredient list and demo_ingredients.json format
        if "products" in data:
            product_key = args.product or data.get("default_demo", list(data["products"].keys())[0])
            product = data["products"][product_key]
            product_name = product["product_name"]
            ingredients = product["ingredients_parsed"]
        elif "ingredients_parsed" in data:
            product_name = data.get("product_name", "Unknown Product")
            ingredients = data["ingredients_parsed"]
        elif "ingredients" in data:
            product_name = data.get("product_name", "Unknown Product")
            ingredients = data["ingredients"]
        else:
            print("[ERROR] JSON must contain 'ingredients', 'ingredients_parsed', or 'products'", file=sys.stderr)
            sys.exit(1)
    else:
        parser.error("Provide --ingredients, --input, or --demo")

    if not ingredients:
        print("[ERROR] No ingredients found to analyse", file=sys.stderr)
        sys.exit(1)

    print(f"[NutriDex] Product: {product_name}")
    print(f"[NutriDex] Ingredients to analyse: {len(ingredients)}")

    # ── Lookup ────────────────────────────────────────────────────────────────
    print("[NutriDex] Loading additive database ...")
    db = AdditiveDB()
    print(f"[NutriDex] Database loaded: {len(db.entries)} additives")

    print("[NutriDex] Looking up ingredients ...")
    lookup_results = db.lookup_all(ingredients)

    matched = sum(1 for r in lookup_results if r["matched"])
    print(f"[NutriDex] Matched: {matched}/{len(ingredients)} ingredients")

    # ── Scoring ───────────────────────────────────────────────────────────────
    print("[NutriDex] Computing life impact score ...")
    impact = compute_life_impact(lookup_results)

    print(f"[NutriDex] Raw points: {impact['total_raw_points']:.4f}")
    print(f"[NutriDex] UPF multiplier: {impact['upf_multiplier']}x")
    print(f"[NutriDex] Verdict: {impact['verdict_emoji']} {impact['verdict']}")

    # ── Report ────────────────────────────────────────────────────────────────
    output_dir = Path(args.output)
    print(f"[NutriDex] Generating report in {output_dir}/ ...")

    report_path = generate_report(
        product_name=product_name,
        ingredients=ingredients,
        lookup_results=lookup_results,
        impact=impact,
        output_dir=output_dir,
        figures=not args.no_figures,
    )

    # ── Result JSON ───────────────────────────────────────────────────────────
    print("[NutriDex] Writing result.json ...")
    write_result_json(
        output_dir=output_dir,
        skill="nutridex",
        version="0.1.0",
        summary={
            "product_name": product_name,
            "total_ingredients": impact["total_ingredients"],
            "matched_ingredients": impact["matched_ingredients"],
            "nova4_count": impact["nova4_count"],
            "upf_multiplier": impact["upf_multiplier"],
            "years_off_central": impact["years_off_central"],
            "years_off_range": [impact["years_off_optimistic"], impact["years_off_pessimistic"]],
            "verdict": impact["verdict"],
            "iarc_flagged_count": len(impact["iarc_flagged"]),
        },
        data={
            "impact": impact,
            "lookup_results": [
                {
                    "ingredient": r["ingredient"],
                    "matched": r["matched"],
                    "e_number": r["entry"].get("id", "") if r["entry"] else "",
                    "category": r["entry"].get("category", "") if r["entry"] else "",
                }
                for r in lookup_results
            ],
        },
    )

    print(f"\n[NutriDex] Done!")
    print(f"[NutriDex] Report: {report_path}")
    print(f"[NutriDex] Results in: {output_dir}/")
    print(f"\n[NutriDex] {impact['verdict_emoji']} {impact['verdict']}: "
          f"-{impact['years_off_central']:.2f} years (central estimate)")


if __name__ == "__main__":
    main()
