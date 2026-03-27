#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClawBio Variant Scorer
On-device deep learning variant scoring using HyenaDNA.

Scores DNA variants from 23andMe or VCF files for functional disruption
using the HyenaDNA-small-32k foundation model (30M params, ~43 MB).
No reference genome or data upload required.

Usage:
    python variant_scorer.py --input patient_data.txt --output report_dir
    python variant_scorer.py --demo --output /tmp/dlscore_demo
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared library imports
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from clawbio.common.parsers import parse_genetic_file
from clawbio.common.checksums import sha256_file
from clawbio.common.report import (
    write_result_json,
    generate_report_header,
    generate_report_footer,
    DISCLAIMER,
)

import clinvar as _clinvar
import flanking as _flanking
import model as _model

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SKILL_NAME = "variant-scorer"
SKILL_VERSION = "0.1.0"
DEFAULT_THRESHOLD = 0.5
DEMO_SCORES_PATH = _SKILL_DIR / "data" / "demo_scores.json"


# ---------------------------------------------------------------------------
# Scoring pipeline
# ---------------------------------------------------------------------------


def score_variants_live(
    genotypes: dict,
    flanking_data: dict,
    max_variants: int | None = None,
) -> list[dict]:
    """Score variants using live HyenaDNA inference.

    Args:
        genotypes: {rsid: GenotypeRecord} from parse_genetic_file().
        flanking_data: Flanking sequences dict (from API, cache, or bundled).
        max_variants: Limit number of variants to score.

    Returns:
        List of scored variant dicts.
    """
    hyena_model, tokenizer = _model.load_model()

    # Match genotypes to flanking data
    matched = []
    for rsid, record in genotypes.items():
        info = flanking_data.get(rsid)
        if info is None:
            continue
        matched.append((rsid, record, info))

    if max_variants:
        matched = matched[:max_variants]

    results = []
    total = len(matched)
    for i, (rsid, record, info) in enumerate(matched, 1):
        gt = record.genotype.upper()
        ref = info["ref"]

        # Determine alt allele from genotype
        alt = _determine_alt(gt, ref)
        if alt is None:
            continue

        print(
            f"  Scoring {rsid} ({info['gene']}) [{i}/{total}]...",
            file=sys.stderr,
        )

        scores = _model.score_variant(
            hyena_model,
            tokenizer,
            info["context"],
            center_pos=info["flank"],
            ref=ref,
            alt=alt,
        )

        results.append({
            "rsid": rsid,
            "gene": info["gene"],
            "chrom": info["chrom"],
            "pos": info["pos"],
            "ref": ref,
            "alt": alt,
            "genotype": gt,
            **scores,
        })

    # Sort by disruption score descending
    results.sort(key=lambda x: x["disruption_score"], reverse=True)
    return results


def _determine_alt(genotype: str, ref: str) -> str | None:
    """Determine the alt allele from a genotype string and ref allele.

    Returns the non-ref allele, or ref if homozygous ref, or None if
    the genotype cannot be interpreted.
    """
    if not genotype or genotype in ("--", "00", "DD", "II"):
        return None

    alleles = set(genotype)
    non_ref = alleles - {ref}

    if not non_ref:
        # Homozygous reference — score as ref vs ref (score = 0)
        return ref

    # Return the first non-ref allele
    return sorted(non_ref)[0]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    scored_variants: list[dict],
    output_dir: Path,
    input_path: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    is_demo: bool = False,
) -> None:
    """Generate the variant scoring report and all output files.

    Args:
        scored_variants: List of scored variant dicts from scoring pipeline.
        output_dir: Directory to write outputs.
        input_path: Path to input file (for header metadata).
        threshold: Minimum disruption score to highlight.
        is_demo: Whether this is a demo run.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Summary stats ──
    n_total = len(scored_variants)
    n_above = sum(1 for v in scored_variants if v["disruption_score"] >= threshold)
    tier_counts = {"high": 0, "moderate": 0, "low": 0, "benign": 0}
    for v in scored_variants:
        tier_counts[v["tier"]] = tier_counts.get(v["tier"], 0) + 1

    # ClinVar summary
    clinvar_counts = {}
    for v in scored_variants:
        cat = v.get("clinvar_category", "not_in_clinvar")
        clinvar_counts[cat] = clinvar_counts.get(cat, 0) + 1

    summary = {
        "variants_scored": n_total,
        "variants_above_threshold": n_above,
        "threshold": threshold,
        "tier_counts": tier_counts,
        "clinvar_counts": clinvar_counts,
        "model": _model.MODEL_ID,
        "is_demo": is_demo,
    }

    # ── report.md ──
    extra_meta = {
        "Model": _model.MODEL_ID,
        "Threshold": str(threshold),
    }
    if is_demo:
        extra_meta["Mode"] = "Demo (pre-computed scores)"

    header = generate_report_header(
        title="Variant Disruption Score Report",
        skill_name=SKILL_NAME,
        input_files=[input_path] if input_path else None,
        extra_metadata=extra_meta,
    )

    body_lines = [
        "## Summary\n",
        f"- **Variants scored**: {n_total}",
        f"- **Above threshold** (>= {threshold}): {n_above}",
        f"- **High disruption**: {tier_counts['high']}",
        f"- **Moderate disruption**: {tier_counts['moderate']}",
        f"- **Low disruption**: {tier_counts['low']}",
        f"- **Benign**: {tier_counts['benign']}",
        "",
    ]

    # ClinVar summary if present
    if any(v.get("clinvar_significance") for v in scored_variants):
        body_lines.extend([
            "### ClinVar Clinical Significance\n",
        ])
        for cat, count in sorted(clinvar_counts.items()):
            label = cat.replace("_", " ").title()
            body_lines.append(f"- **{label}**: {count}")
        body_lines.append("")

    # ── Main table ──
    has_clinvar = any(v.get("clinvar_significance") for v in scored_variants)
    if has_clinvar:
        body_lines.extend([
            "## Variant Scores\n",
            "| rsID | Gene | Genotype | DL Score | Tier | ClinVar | Conditions |",
            "|------|------|----------|----------|------|---------|------------|",
        ])
        for v in scored_variants:
            score_str = f"{v['disruption_score']:.2f}"
            tier_marker = ""
            if v["tier"] == "high":
                tier_marker = " **HIGH**"
            elif v["tier"] == "moderate":
                tier_marker = " *mod*"
            clinvar_sig = v.get("clinvar_significance", "—")
            conditions = v.get("clinvar_conditions", [])
            cond_str = "; ".join(conditions[:2]) if conditions else "—"
            body_lines.append(
                f"| {v['rsid']} | {v['gene']} | {v['genotype']} "
                f"| {score_str} | {v['tier']}{tier_marker} "
                f"| {clinvar_sig} | {cond_str} |"
            )
    else:
        body_lines.extend([
            "## Variant Scores\n",
            "| rsID | Gene | Chrom | Pos | Ref | Alt | Genotype | Score | Tier |",
            "|------|------|-------|-----|-----|-----|----------|-------|------|",
        ])
        for v in scored_variants:
            score_str = f"{v['disruption_score']:.2f}"
            tier_marker = ""
            if v["tier"] == "high":
                tier_marker = " **HIGH**"
            elif v["tier"] == "moderate":
                tier_marker = " *moderate*"
            body_lines.append(
                f"| {v['rsid']} | {v['gene']} | {v['chrom']} | {v['pos']} "
                f"| {v['ref']} | {v['alt']} | {v['genotype']} "
                f"| {score_str} | {v['tier']}{tier_marker} |"
            )

    body_lines.extend([
        "",
        "## Methodology\n",
        f"Variants were scored using the **HyenaDNA-small-32k** DNA foundation model "
        f"({_model.MODEL_ID}). For each variant, the model computes the log-probability "
        f"of the reference vs alternate allele at the variant position, given the "
        f"surrounding ~1 kb DNA context fetched from the Ensembl REST API. "
        f"The disruption score is the absolute log-odds difference.",
        "",
    ])

    if has_clinvar:
        body_lines.extend([
            "**ClinVar** clinical significance classifications were queried from "
            "the NCBI ClinVar database via the E-utilities API. ClinVar provides "
            "expert-curated pathogenicity assessments based on clinical evidence.",
            "",
        ])

    body_lines.extend([
        "**Tier thresholds (HyenaDNA DL score):**",
        f"- High: score >= {_model.TIER_THRESHOLDS['high']}",
        f"- Moderate: score >= {_model.TIER_THRESHOLDS['moderate']}",
        f"- Low: score >= {_model.TIER_THRESHOLDS['low']}",
        f"- Benign: score < {_model.TIER_THRESHOLDS['low']}",
        "",
        "The DL disruption score measures how unexpected the substitution is "
        "in local DNA context. ClinVar significance reflects clinical evidence "
        "of disease association. These are complementary signals.",
    ])

    footer = generate_report_footer()
    report_md = header + "\n".join(body_lines) + footer

    (output_dir / "report.md").write_text(report_md)

    # ── scores.tsv ──
    tsv_path = output_dir / "scores.tsv"
    with open(tsv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "rsid", "gene", "chrom", "pos", "ref", "alt",
                "genotype", "disruption_score", "tier",
                "log_likelihood_ref", "log_likelihood_alt",
                "clinvar_significance", "clinvar_conditions",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        for v in scored_variants:
            conditions = v.get("clinvar_conditions", [])
            writer.writerow({
                "rsid": v["rsid"],
                "gene": v["gene"],
                "chrom": v["chrom"],
                "pos": v["pos"],
                "ref": v["ref"],
                "alt": v["alt"],
                "genotype": v["genotype"],
                "disruption_score": round(v["disruption_score"], 4),
                "tier": v["tier"],
                "log_likelihood_ref": round(v.get("log_likelihood_ref", 0), 4),
                "log_likelihood_alt": round(v.get("log_likelihood_alt", 0), 4),
                "clinvar_significance": v.get("clinvar_significance", ""),
                "clinvar_conditions": "; ".join(conditions) if conditions else "",
            })

    # ── result.json ──
    input_checksum = ""
    if input_path and Path(input_path).exists():
        input_checksum = sha256_file(input_path)

    write_result_json(
        output_dir=output_dir,
        skill=SKILL_NAME,
        version=SKILL_VERSION,
        summary=summary,
        data={"scored_variants": scored_variants},
        input_checksum=input_checksum,
    )

    # ── reproducibility ──
    repro_dir = output_dir / "reproducibility"
    repro_dir.mkdir(exist_ok=True)
    cmd_parts = ["python", "skills/variant-scorer/variant_scorer.py"]
    if is_demo:
        cmd_parts.append("--demo")
    if input_path:
        cmd_parts.extend(["--input", str(input_path)])
    cmd_parts.extend(["--output", str(output_dir)])
    (repro_dir / "commands.sh").write_text(
        "#!/bin/bash\n" + " ".join(cmd_parts) + "\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="On-device DL variant scoring using HyenaDNA",
    )
    parser.add_argument(
        "--input", "-i",
        help="Path to 23andMe or VCF file",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output directory for report",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with pre-computed demo scores (no torch/transformers needed)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum disruption score to highlight (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--max-variants",
        type=int,
        default=None,
        help="Maximum number of variants to score",
    )
    parser.add_argument(
        "--assembly",
        default="GRCh37",
        choices=["GRCh37", "GRCh38"],
        help="Reference assembly for flanking sequence lookup (default: GRCh37)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip Ensembl API calls; use only cached or bundled flanking sequences",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)

    if args.demo:
        # Demo mode — load pre-computed scores, no dependencies needed
        print("Running variant scorer in demo mode...", file=sys.stderr)

        if not DEMO_SCORES_PATH.exists():
            print(
                f"Error: demo scores not found at {DEMO_SCORES_PATH}",
                file=sys.stderr,
            )
            sys.exit(1)

        demo_data = json.loads(DEMO_SCORES_PATH.read_text())
        scored_variants = demo_data["variants"]

        generate_report(
            scored_variants=scored_variants,
            output_dir=output_dir,
            threshold=args.threshold,
            is_demo=True,
        )

        print(f"\nDemo report written to {output_dir}/", file=sys.stderr)
        print(f"  report.md   — {len(scored_variants)} variants scored", file=sys.stderr)
        print(f"  scores.tsv  — tabular scores", file=sys.stderr)
        print(f"  result.json — machine-readable results", file=sys.stderr)
        return

    # Live mode — requires torch + transformers
    if not args.input:
        print("Error: --input required (or use --demo)", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Check dependencies before loading anything
    ok, msg = _model.check_dependencies()
    if not ok:
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    print(f"Scoring variants from {input_path.name}...", file=sys.stderr)

    # Parse input
    genotypes = parse_genetic_file(input_path)
    print(f"  Parsed {len(genotypes)} variants from input", file=sys.stderr)

    # Load bundled panel to know which rsIDs we can score
    bundled = _flanking.load_flanking_sequences()
    panel_rsids = set(bundled.keys())

    # Filter to panel variants that are in the input
    panel_genotypes = {
        rsid: rec for rsid, rec in genotypes.items() if rsid in panel_rsids
    }
    print(
        f"  Matched {len(panel_genotypes)}/{len(panel_rsids)} panel variants",
        file=sys.stderr,
    )

    if not panel_genotypes:
        print(
            "Warning: no input variants matched the scoring panel. "
            "The panel covers pharmacogenomic SNPs (CYP2D6, CYP2C19, etc.).",
            file=sys.stderr,
        )

    # Fetch real flanking sequences (API → cache → bundled fallback)
    if not args.offline:
        print(
            "  Fetching flanking sequences from Ensembl REST API "
            "(only public coordinates sent, no patient data)...",
            file=sys.stderr,
        )
    flanking_data = _flanking.load_or_fetch_panel(
        variants=panel_genotypes,
        assembly=args.assembly,
        offline=args.offline,
    )
    sources = {}
    for entry in flanking_data.values():
        src = entry.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    if sources:
        src_str = ", ".join(f"{v} {k}" for k, v in sorted(sources.items()))
        print(f"  Flanking sources: {src_str}", file=sys.stderr)

    # Score
    scored_variants = score_variants_live(
        genotypes=genotypes,
        flanking_data=flanking_data,
        max_variants=args.max_variants,
    )

    # ClinVar lookup
    if not args.offline:
        print(
            "  Looking up ClinVar clinical significance "
            "(only rsIDs sent, no patient data)...",
            file=sys.stderr,
        )
    rsid_list = [v["rsid"] for v in scored_variants]
    clinvar_data = _clinvar.lookup_batch(rsid_list, offline=args.offline)

    for v in scored_variants:
        cv = clinvar_data.get(v["rsid"])
        if cv:
            v["clinvar_significance"] = cv.get("clinical_significance", "")
            v["clinvar_conditions"] = cv.get("conditions", [])
            v["clinvar_category"] = _clinvar.significance_to_category(
                cv.get("clinical_significance", "")
            )
        else:
            v["clinvar_significance"] = ""
            v["clinvar_conditions"] = []
            v["clinvar_category"] = "not_in_clinvar"

    # Generate report
    generate_report(
        scored_variants=scored_variants,
        output_dir=output_dir,
        input_path=input_path,
        threshold=args.threshold,
    )

    # Print summary
    n_above = sum(
        1 for v in scored_variants if v["disruption_score"] >= args.threshold
    )
    print(f"\nReport written to {output_dir}/", file=sys.stderr)
    print(f"  {len(scored_variants)} variants scored", file=sys.stderr)
    print(
        f"  {n_above} above threshold ({args.threshold})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
