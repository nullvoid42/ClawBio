"""Importable API for the variant-scorer skill.

Provides a programmatic interface that other skills (e.g. bio-orchestrator)
and the PatientProfile system can call without shelling out to the CLI.

Usage (from project root)::

    import sys; sys.path.insert(0, ".")
    sys.path.insert(0, "skills/variant-scorer")
    from api import run

    result = run({"rs4244285": "AG", "rs9923231": "TT", ...})
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path so clawbio.common imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# The skill directory uses a hyphen ("variant-scorer") which is not a valid
# Python package name.  Add the skill dir to sys.path for direct import.
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

import flanking as _flanking
import model as _model
import variant_scorer as _scorer


def run(genotypes: dict[str, str] | None = None, options: dict | None = None) -> dict:
    """Run variant scoring on a genotype dict.

    Args:
        genotypes: Mapping of rsid -> genotype string (e.g. ``{"rs4244285": "AG"}``).
                   If None, runs demo mode with pre-computed scores.
        options: Optional dict with:
            - output_dir: str or Path — write report/result.json here
            - threshold: float — minimum disruption score to highlight (default 0.5)
            - max_variants: int — limit variants scored
            - demo: bool — force demo mode

    Returns:
        dict with keys:
            - scored_variants: list of scored variant dicts
            - summary: high-level counts and model info
    """
    options = options or {}
    threshold = options.get("threshold", _scorer.DEFAULT_THRESHOLD)
    max_variants = options.get("max_variants")
    output_dir = options.get("output_dir")
    is_demo = options.get("demo", genotypes is None)

    if is_demo:
        # Demo mode — load pre-computed scores
        demo_data = json.loads(_scorer.DEMO_SCORES_PATH.read_text())
        scored_variants = demo_data["variants"]
    else:
        # Build GenotypeRecord-like objects for the scorer
        from clawbio.common.parsers import GenotypeRecord

        records = {}
        for rsid, gt in genotypes.items():
            if gt and gt not in ("--", "00"):
                records[rsid] = GenotypeRecord(genotype=gt.upper())

        flanking_data = _flanking.load_flanking_sequences()
        scored_variants = _scorer.score_variants_live(
            genotypes=records,
            flanking_data=flanking_data,
            max_variants=max_variants,
        )

    # Compute summary
    tier_counts = {"high": 0, "moderate": 0, "low": 0, "benign": 0}
    for v in scored_variants:
        tier_counts[v["tier"]] = tier_counts.get(v["tier"], 0) + 1

    summary = {
        "variants_scored": len(scored_variants),
        "variants_above_threshold": sum(
            1 for v in scored_variants if v["disruption_score"] >= threshold
        ),
        "threshold": threshold,
        "tier_counts": tier_counts,
        "model": _model.MODEL_ID,
        "is_demo": is_demo,
    }

    # Write outputs if output_dir specified
    if output_dir:
        _scorer.generate_report(
            scored_variants=scored_variants,
            output_dir=Path(output_dir),
            threshold=threshold,
            is_demo=is_demo,
        )

    return {
        "scored_variants": scored_variants,
        "summary": summary,
    }
