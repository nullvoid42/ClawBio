# -*- coding: utf-8 -*-
"""generate_report.py — Markdown report and matplotlib figures for NutriDex."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def generate_report(
    product_name: str,
    ingredients: list[str],
    lookup_results: list[dict[str, Any]],
    impact: dict[str, Any],
    output_dir: str | Path,
    figures: bool = True,
) -> str:
    """Generate the NutriDex markdown report and optional figures.

    Returns path to the report file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        "# NutriDex Additive Analysis Report",
        "",
        f"**Product**: {product_name}",
        f"**Generated**: {timestamp}",
        "**Tool**: ClawBio NutriDex v0.1.0",
        f"**Total Ingredients Analysed**: {impact['total_ingredients']}",
        f"**Database Matches**: {impact['matched_ingredients']}/{impact['total_ingredients']}",
        "",
        "> **Disclaimer**: ClawBio is a research and educational tool. It is not a medical "
        "device and does not provide clinical diagnoses. Consult a healthcare professional "
        "before making any medical decisions. Life impact estimates are illustrative, based "
        "on population-level epidemiological associations, and should not be interpreted as "
        "individual predictions.",
        "",
        "---",
        "",
    ]

    # ── Act I: The Ingredient Census ──────────────────────────────────────────
    lines += [
        "## Act I: The Ingredient Census",
        "",
        f"Your **{product_name}** contains **{len(ingredients)}** ingredients:",
        "",
    ]
    for i, ing in enumerate(ingredients, 1):
        lines.append(f"{i}. {ing}")
    lines += ["", "---", ""]

    # ── Act II: The Full Breakdown ────────────────────────────────────────────
    lines += [
        "## Act II: The Full Breakdown",
        "",
        "| # | Ingredient | E-Number | Category | NOVA | Risk Points | Key Concern |",
        "|---|-----------|----------|----------|:----:|:-----------:|-------------|",
    ]
    for i, score in enumerate(impact["ingredient_scores"], 1):
        e_num = score.get("e_number", "—")
        if not e_num or e_num == "":
            e_num = "—"
        cat = score.get("category", "unknown")
        nova = score.get("nova_class")
        nova_str = str(nova) if nova else "—"
        pts = score["points"]
        pts_str = f"{pts:.2f}" if pts > 0 else "0.00"
        concern = score.get("health_effects", score.get("note", "—"))
        # Truncate long concerns
        if len(concern) > 80:
            concern = concern[:77] + "..."
        lines.append(f"| {i} | {score['ingredient']} | {e_num} | {cat} | {nova_str} | {pts_str} | {concern} |")
    lines += ["", ""]

    # ── E-Number Spotlight ────────────────────────────────────────────────────
    flagged = [s for s in impact["ingredient_scores"] if s["points"] > 0.05]
    if flagged:
        lines += [
            "### Spotlight: Highest-Risk Additives",
            "",
        ]
        for s in sorted(flagged, key=lambda x: -x["points"]):
            lines += [
                f"**{s['ingredient']}** ({s.get('e_number', 'N/A')})",
                f"- Category: {s.get('category', 'unknown')}",
                f"- Risk Points: {s['points']:.2f}",
                f"- {s.get('health_effects', '')}",
            ]
            if s.get("fun_fact"):
                lines.append(f"- *{s['fun_fact']}*")
            lines.append("")
        lines += ["---", ""]

    # ── IARC Flagged ──────────────────────────────────────────────────────────
    if impact["iarc_flagged"]:
        lines += [
            "### IARC Carcinogenicity Flags",
            "",
            "| Ingredient | E-Number | IARC Group | Meaning |",
            "|-----------|----------|:----------:|---------|",
        ]
        iarc_meanings = {
            "1": "Carcinogenic to humans",
            "2A": "Probably carcinogenic",
            "2B": "Possibly carcinogenic",
            "3": "Not classifiable",
        }
        for item in impact["iarc_flagged"]:
            group = item["iarc_group"]
            meaning = iarc_meanings.get(group, group)
            lines.append(f"| {item['ingredient']} | {item['e_number']} | {group} | {meaning} |")
        lines += ["", "---", ""]

    # ── UPF Assessment ────────────────────────────────────────────────────────
    lines += [
        "## Ultra-Processing Assessment",
        "",
        f"- **NOVA-4 (ultra-processed) ingredients**: {impact['nova4_count']}/{impact['total_ingredients']}",
        f"- **UPF multiplier applied**: {impact['upf_multiplier']}x",
        "",
    ]
    if impact["upf_multiplier"] > 1.0:
        lines += [
            "> The high concentration of ultra-processed ingredients triggers a multiplier ",
            "> based on the BMJ 2019 NutriNet-Sante cohort (Schnabel et al., HR 1.14 per 10% UPF increase) ",
            "> and JAMA 2019 (Rico-Campa et al., HR 1.62 highest vs lowest UPF quartile).",
            "",
        ]
    lines += ["---", ""]

    # ── Act III: The Verdict ──────────────────────────────────────────────────
    lines += [
        "## Act III: The Verdict",
        "",
        f"### {impact['verdict_emoji']} {impact['verdict']}",
        "",
        f"*{impact['verdict_text']}*",
        "",
        "### Life Impact Score",
        "",
        f"If consumed **daily** over a remaining life expectancy of ~47 years:",
        "",
        f"| Scenario | Estimated Impact |",
        f"|----------|:----------------:|",
        f"| Optimistic | -{impact['years_off_optimistic']:.2f} years |",
        f"| **Central estimate** | **-{impact['years_off_central']:.2f} years** |",
        f"| Pessimistic | -{impact['years_off_pessimistic']:.2f} years |",
        "",
    ]

    # Gauge visualisation in text
    gauge_pos = min(int(impact["years_off_central"] / 1.0 * 20), 20)
    gauge_bar = "=" * gauge_pos + ">" + " " * (20 - gauge_pos)
    lines += [
        "```",
        "Life Impact Gauge",
        f"[{gauge_bar}] {impact['years_off_central']:.2f} years",
        " 0.0                              1.0+",
        " Safe        Caution      Danger  ",
        "```",
        "",
        "---",
        "",
    ]

    # ── But Actually... ───────────────────────────────────────────────────────
    lines += [
        "## But Actually...",
        "",
        "Before you throw this snack in the bin, some context:",
        "",
        "- These estimates assume **daily consumption** of this exact product for decades",
        "- Occasional treats are part of a balanced life — dose makes the poison",
        "- The scoring model uses population-level associations, not individual risk",
        "- Physical activity, sleep, stress, and overall diet pattern matter far more",
        "  than any single snack",
        "- The epidemiological evidence (BMJ/JAMA studies) shows *associations*, not",
        "  proven causation for individual additives",
        "",
        "**Bottom line**: An occasional packet won't kill you. A daily habit of",
        "ultra-processed foods, across all meals, is where the risk accumulates.",
        "",
        "---",
        "",
    ]

    # ── Category Breakdown ────────────────────────────────────────────────────
    if impact["category_breakdown"]:
        lines += [
            "## Ingredient Category Breakdown",
            "",
            "| Category | Count |",
            "|----------|:-----:|",
        ]
        for cat, count in sorted(impact["category_breakdown"].items(), key=lambda x: -x[1]):
            lines.append(f"| {cat} | {count} |")
        lines += ["", "---", ""]

    # ── Methodology ───────────────────────────────────────────────────────────
    lines += [
        "## Methodology",
        "",
        "- **Additive database**: EFSA re-evaluations, IARC Monographs, NOVA classification (Monteiro 2016), FDA GRAS",
        "- **Life impact model**: Based on life_impact_points (0.0-0.5 per additive), scaled by UPF multiplier and remaining life expectancy",
        "- **UPF multiplier**: Grounded in BMJ 2019 NutriNet-Sante (HR 1.14/10% UPF) and JAMA 2019 (HR 1.62 top vs bottom UPF quartile)",
        "- **Scoring is illustrative**: Designed to inform and entertain, not to provide clinical guidance",
        "",
    ]

    # ── Footer ────────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## Disclaimer",
        "",
        "*ClawBio is a research and educational tool. It is not a medical device "
        "and does not provide clinical diagnoses. Consult a healthcare professional "
        "before making any medical decisions.*",
        "",
    ]

    report_text = "\n".join(lines)
    report_path = output_dir / "nutridex_report.md"
    report_path.write_text(report_text, encoding="utf-8")

    if figures:
        _generate_figures(impact, output_dir)

    return str(report_path)


def _generate_figures(impact: dict[str, Any], output_dir: Path) -> None:
    """Generate speedometer gauge and horizontal bar chart."""
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[NutriDex] matplotlib/numpy not available — skipping figures")
        return

    # ── Speedometer Gauge ─────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 5), subplot_kw={"projection": "polar"})
    ax.set_theta_offset(np.pi)
    ax.set_theta_direction(-1)
    ax.set_thetamin(0)
    ax.set_thetamax(180)

    # Background zones
    zones = [
        (0.0, 0.05, "#2ecc71", "Safe"),
        (0.05, 0.15, "#f1c40f", "Caution"),
        (0.15, 0.30, "#e67e22", "Danger"),
        (0.30, 0.50, "#e74c3c", "Severe"),
        (0.50, 1.00, "#8e44ad", "Extreme"),
    ]
    max_val = 1.0
    for start, end, colour, _label in zones:
        theta_start = (start / max_val) * np.pi
        theta_end = (end / max_val) * np.pi
        theta_range = np.linspace(theta_start, theta_end, 50)
        ax.fill_between(theta_range, 0.6, 1.0, color=colour, alpha=0.6)

    # Needle
    value = min(impact["years_off_central"], max_val)
    needle_angle = (value / max_val) * np.pi
    ax.annotate(
        "",
        xy=(needle_angle, 0.95),
        xytext=(needle_angle, 0.0),
        arrowprops=dict(arrowstyle="->", color="black", lw=2.5),
    )

    # Centre dot
    ax.plot(0, 0, "ko", markersize=8)

    # Labels
    for start, end, _colour, label in zones:
        mid = ((start + end) / 2 / max_val) * np.pi
        ax.text(mid, 1.15, label, ha="center", va="center", fontsize=8, fontweight="bold")

    ax.set_rticks([])
    ax.set_xticks([])
    ax.spines["polar"].set_visible(False)
    ax.set_title(
        f"Life Impact: {impact['years_off_central']:.2f} years\n"
        f"{impact['verdict']}",
        fontsize=14, fontweight="bold", pad=20, y=1.08,
    )
    plt.tight_layout()
    fig.savefig(output_dir / "nutridex_gauge.png", dpi=150, bbox_inches="tight")
    plt.close()

    # ── Horizontal Bar Chart (Ingredient Risk) ────────────────────────────────
    scored = [s for s in impact["ingredient_scores"] if s["points"] > 0]
    if not scored:
        return

    scored.sort(key=lambda x: x["points"])
    labels = [s["ingredient"][:30] for s in scored]
    values = [s["points"] for s in scored]

    # Colour by risk level
    colours = []
    for v in values:
        if v <= 0.03:
            colours.append("#2ecc71")
        elif v <= 0.08:
            colours.append("#f1c40f")
        elif v <= 0.15:
            colours.append("#e67e22")
        else:
            colours.append("#e74c3c")

    fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.5)))
    bars = ax.barh(labels, values, color=colours, edgecolor="white", linewidth=0.5)

    ax.set_xlabel("Risk Points", fontsize=11)
    ax.set_title("Ingredient Risk Breakdown", fontsize=14, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Value labels on bars
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.005, bar.get_y() + bar.get_height() / 2,
                f"{val:.2f}", va="center", fontsize=9)

    plt.tight_layout()
    fig.savefig(output_dir / "nutridex_breakdown.png", dpi=150, bbox_inches="tight")
    plt.close()
