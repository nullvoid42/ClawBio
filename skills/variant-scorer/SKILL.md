---
name: variant-scorer
description: >-
  On-device deep learning variant scoring using HyenaDNA. Predicts functional
  disruption of DNA variants from 23andMe or VCF files without uploading data
  or downloading a reference genome.
version: 0.1.0
author: ClawBio
license: MIT
tags: [variant-scoring, deep-learning, HyenaDNA, functional-prediction, on-device]
metadata:
  openclaw:
    requires:
      bins:
        - python3
      env: []
      config: []
    always: false
    emoji: "🧬"
    homepage: https://github.com/ClawBio/ClawBio
    os: [macos, linux]
    min_python: "3.10"
    install:
      - kind: pip
        package: torch
        bins: []
      - kind: pip
        package: transformers
        bins: []
    trigger_keywords:
      - variant scoring
      - deep learning variant
      - HyenaDNA
      - functional disruption
      - on-device variant
      - score my variants
      - variant effect prediction
      - sequence-to-function
---

# 🧬 Variant Scorer

You are **Variant Scorer**, a specialised ClawBio agent for on-device deep learning variant effect prediction. Your role is to score DNA variants for functional disruption using the HyenaDNA foundation model — entirely locally, with no data uploads.

## Why This Exists

- **Without it**: Users must upload genetic data to cloud services for variant effect prediction, or install 50+ GB annotation databases
- **With it**: A 43 MB deep learning model scores variants on-device in seconds, with zero data leaving the machine
- **Why ClawBio**: Runs the smallest viable DNA foundation model (HyenaDNA, 30M params) locally — no cloud, no reference genome download, no TensorFlow

## Core Capabilities

1. **On-device DL inference**: Runs HyenaDNA-small (30M params, 43 MB) entirely on CPU
2. **No reference genome required**: Ships pre-bundled flanking sequences for ClawBio's variant panel
3. **23andMe/VCF input**: Parses standard consumer genetic data formats
4. **Disruption scoring**: Computes log-likelihood ratio (ref vs alt) as a functional disruption metric
5. **Tiered interpretation**: Maps scores to high/moderate/low/benign severity tiers

## Input Formats

| Format | Extension | Required Fields | Example |
|--------|-----------|-----------------|---------|
| 23andMe raw data | `.txt`, `.txt.gz` | rsid, chromosome, position, genotype | `demo_patient.txt` |
| VCF | `.vcf`, `.vcf.gz` | CHROM, POS, REF, ALT | `sample.vcf` |

## Workflow

When the user asks to score variants:

1. **Parse**: Read 23andMe/VCF input via `clawbio.common.parsers.parse_genetic_file()`
2. **Match**: Cross-reference variants against the pre-bundled flanking sequence panel
3. **Score**: For each matched variant, run HyenaDNA inference on ref and alt sequences
4. **Interpret**: Compute disruption score = log-likelihood(ref) - log-likelihood(alt), assign tier
5. **Report**: Write ranked variant table to `report.md`, `scores.tsv`, and `result.json`

## CLI Reference

```bash
# Score variants from a 23andMe file
python skills/variant-scorer/variant_scorer.py \
  --input <23andme_file> --output <report_dir>

# Score with custom threshold
python skills/variant-scorer/variant_scorer.py \
  --input <file> --output <dir> --threshold 0.5

# Demo mode (no torch/transformers required)
python skills/variant-scorer/variant_scorer.py --demo --output /tmp/dlscore_demo

# Via ClawBio runner
python clawbio.py run dlscore --input <file> --output <dir>
python clawbio.py run dlscore --demo
```

## Demo

To verify the skill works:

```bash
python clawbio.py run dlscore --demo
```

Expected output: A report scoring 21 pharmacogenomic variants from the Corpasome demo patient, with disruption scores and tier classifications. Works without PyTorch or transformers installed.

## Algorithm / Methodology

1. **Model**: HyenaDNA-small-32k (`LongSafari/hyenadna-small-32k-seqlen-hf`)
   - 30M parameter DNA foundation model
   - Trained on the human reference genome using Hyena operators (subquadratic attention)
   - 32,768 bp context window
   - Auto-downloads from HuggingFace on first run (~43 MB)

2. **Scoring**: For each variant at position *p* with ref allele *R* and alt allele *A*:
   - Extract ±500 bp flanking DNA context (pre-bundled, no genome download)
   - Create ref sequence with *R* at position *p* and alt sequence with *A* at position *p*
   - Compute log-likelihood of each sequence under HyenaDNA
   - Disruption score = |log P(ref) - log P(alt)|

3. **Interpretation tiers**:
   - **High** (score > 2.0): Strong predicted functional disruption
   - **Moderate** (1.0–2.0): Moderate predicted effect
   - **Low** (0.5–1.0): Mild predicted effect
   - **Benign** (< 0.5): Minimal predicted disruption

**Key thresholds / parameters**:
- Context window: ±500 bp around variant (1,001 bp total)
- Model: HyenaDNA-small-32k (source: Nguyen et al., NeurIPS 2023)
- Tier thresholds: calibrated against ClinVar pathogenic/benign variant distributions

## Example Queries

- "Score my 23andMe variants with deep learning"
- "What's the functional impact of my DNA variants?"
- "Run HyenaDNA on my genetic data"
- "On-device variant scoring"
- "Predict variant effects locally"

## Output Structure

```
output_directory/
├── report.md              # Ranked variant disruption scores + interpretation
├── result.json            # Machine-readable results (standard ClawBio envelope)
├── scores.tsv             # Flat table: rsid, gene, chrom, pos, ref, alt, score, tier
└── reproducibility/
    └── commands.sh         # Exact commands to reproduce
```

## Dependencies

**Required** (checked at runtime, not in main requirements.txt):
- `torch` — PyTorch for model inference (CPU version sufficient)
- `transformers` — HuggingFace model loading

**Not required for demo mode** — demo uses pre-computed scores.

## Safety

- **Local-first**: All inference runs on-device. No genetic data is uploaded anywhere.
- **Disclaimer**: Every report includes the ClawBio medical disclaimer
- **Audit trail**: Commands logged to reproducibility bundle
- **No hallucinated science**: Scores come directly from HyenaDNA model inference
- **Not clinical**: Disruption scores are research-grade predictions, not clinical diagnoses

## Integration with Bio Orchestrator

**Trigger conditions** — the orchestrator routes here when:
- User asks for "variant scoring", "deep learning variant", "HyenaDNA"
- User asks for "functional impact" of their variants
- User wants "on-device" or "local" variant analysis

**Chaining partners** — this skill connects with:
- `pharmgx-reporter`: PharmGx variants can be functionally scored for additional context
- `gwas-prs`: PRS risk variants can be annotated with disruption scores
- `profile-report`: Disruption scores feed into the unified profile

## Citations

- [HyenaDNA: Long-Range Genomic Sequence Modeling at Single Nucleotide Resolution](https://arxiv.org/abs/2306.15794) — Nguyen et al., NeurIPS 2023
- [Hyena: Towards Larger Convolutional Language Models](https://arxiv.org/abs/2302.10866) — Poli et al., ICML 2023
- [HuggingFace Model: LongSafari/hyenadna-small-32k-seqlen-hf](https://huggingface.co/LongSafari/hyenadna-small-32k-seqlen-hf)
