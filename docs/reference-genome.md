# Reference Genome -- Corpas 30x WGS

ClawBio's reference genome is a real, fully open personal genome: the 30x whole-genome sequence of Manuel Corpas, published under CC0. It serves as the default dataset for demos, tutorials, and regression testing across all ClawBio skills.

## About this genome

This is a fully open personal genome sequenced at 30x depth on Illumina by Dante Labs, aligned to GRCh37. It contains SNPs, indels, structural variants (DEL, DUP, INV, BND, INS), and CNVs with QC metrics (Ti/Tv ~ 2.0, Het/Hom ~ 1.6). Licensed CC0 for unrestricted reuse, it serves as ClawBio's default example genome for demos, tutorials, and regression testing. This dataset is provided for research and educational purposes only; it must not be used for clinical decision-making.

> ClawBio is a research and educational tool. It is not a medical device and does not provide clinical diagnoses. Consult a healthcare professional before making any medical decisions.

## Dataset

| Property | Value |
|----------|-------|
| Subject | Manuel Corpas |
| Platform | Illumina (30x coverage) |
| Reference build | GRCh37 (hg19) |
| Sequencing provider | Dante Labs |
| Variant types | SNPs, indels, SVs (DEL, DUP, INV, BND, INS), CNVs |
| QC metrics | Ti/Tv ~ 2.0, Het/Hom ~ 1.6 |
| License | CC0 1.0 Universal (Public Domain) |

### Files on Zenodo

| File | Size | Contents |
|------|------|----------|
| `15001711233855A.all_chroms.snp.vcf.gz` | 174 MB | Genome-wide SNP calls |
| `15001711233855A.all_chroms.indel.vcf.gz` | 50 MB | Genome-wide indel calls |
| `15001711233855A.all_chroms.sv.pass.vcf.gz` | 865 KB | Structural variants (PASS) |
| `15001711233855A.all_chroms.cnv.vcf.gz` | 95 KB | Copy number variants |
| `15001711233855A.snp.vcf` | 65 KB | SNP summary |
| `15001711233855A.indel.vcf` | 65 KB | Indel summary |

## Use cases

### 1. Personal Genome QC Dashboard

Load the variant calls and get an instant quality overview: variant counts by type, Ti/Tv ratio, Het/Hom ratio, chromosome distribution, and pass/fail flags. The "first thing you run" after importing any genome.

**Skills:** VCF parsing, QC metric computation, profile-report

**Try it:**
- "Show me the quality metrics for the reference genome"
- "Is the Ti/Tv ratio within expected range for a 30x WGS?"

### 2. Pharmacogenomics Deep Dive

Run the full PharmGx pipeline on a real genome instead of a demo snippet. Star-allele calls across 12 genes, drug interaction cards for 51 medications, and a personalised prescribing report.

**Skills:** pharmgx-reporter, clinpgx, drug-photo, profile-report

**Try it:**
- `clawbio pharmgx --vcf corpas-30x/subsets/pgx_loci.vcf.gz`
- "What pharmacogenomic variants does the reference genome carry?"

### 3. Structural Variant Exploration

Explore DEL, DUP, INV, BND, and INS calls from a real genome. Filter by type and size, check overlap with genes, and visualise SV distributions. This fills a gap since most demos use SNP/indel data only.

**Skills:** variant-annotation, gwas-lookup, VCF parsing

**Try it:**
- "Show me all structural variants larger than 10kb"
- "Which genes are disrupted by deletions in this genome?"

### 4. Ancestry and Population Context

Run PCA against the SGDP reference panel to see where this European individual clusters. Combine with equity-scorer to show how representation gaps affect interpretation.

**Skills:** claw-ancestry-pca, equity-scorer, genome-compare

**Try it:**
- "Where does the reference genome cluster relative to global populations?"
- "Compare the Corpas genome against George Church's"

### 5. Nutrigenomics and Wellness Report

Generate a complete nutrigenomics profile covering 40 SNPs across 13 dietary domains: caffeine metabolism, lactose tolerance, vitamin needs, fat metabolism, and more.

**Skills:** nutrigx_advisor, profile-report

**Try it:**
- "What does this genome say about caffeine metabolism?"
- "Generate the full wellness report for the reference genome"

### 6. End-to-End Pipeline Regression Test

A single command that runs QC, PharmGx, NutriGx, ancestry, SV summary, and variant annotation, then compares outputs against frozen baselines. The CI integration point.

**Try it:**
```bash
python -m pytest tests/benchmark/test_reference_genome.py -v
```

## Subsets

Lightweight extracts committed to the repository for instant use in tutorials and CI.

| Subset | Contents | Location |
|--------|----------|----------|
| chr20 SNPs + indels | Chromosome 20 variants | `corpas-30x/subsets/chr20_snps_indels.vcf.gz` |
| PGx loci | 31 pharmacogenomic SNPs | `corpas-30x/subsets/pgx_loci.vcf.gz` |
| NutriGx loci | 30 nutrigenomics SNPs | `corpas-30x/subsets/nutrigx_loci.vcf.gz` |
| SV calls | Structural variants (DEL, DUP, INV, BND, INS) | `corpas-30x/subsets/sv_calls.vcf.gz` |
| CNV calls | Copy number variants | `corpas-30x/subsets/cnv_calls.vcf.gz` |

To generate these subsets from the full VCFs:

```bash
python scripts/prepare_corpas_30x.py --download
python scripts/prepare_corpas_30x.py --subsets
```

Requires `bcftools >= 1.17`.

## How to cite / use

**Citation:** Corpas, M. (2026). Personal Whole Genome Sequencing Variant Calls (SNPs, Indels, SVs, CNVs) of Manuel Corpas from Dante Labs 30x WGS. Zenodo. https://doi.org/10.5281/zenodo.19297389

**All versions:** https://doi.org/10.5281/zenodo.19285820

**License:** CC0 1.0 Universal. You may use this data freely for any purpose without restriction.

**In ClawBio:** This dataset is bundled as the default reference genome. Tutorial commands, demos, and regression tests use its subsets unless you supply your own VCF.

**BibTeX:**

```bibtex
@dataset{corpas_2026_wgs,
  author    = {Corpas, Manuel},
  title     = {Personal Whole Genome Sequencing Variant Calls (SNPs, Indels,
               SVs, CNVs) of Manuel Corpas from Dante Labs 30x WGS},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.19297389},
  url       = {https://zenodo.org/records/19297389},
  license   = {CC0-1.0}
}
```

## Relationship to the 23andMe Corpasome

ClawBio has used the Corpasome 23andMe SNP chip (~600,000 variants) as demo data since launch. See [demo-genome.md](demo-genome.md) for that dataset.

The 30x WGS extends this in several ways:

- **Coverage:** ~4 million SNPs and ~600K indels versus ~600K SNP chip positions
- **Variant types:** structural variants and CNVs, not available from SNP arrays
- **Resolution:** every base of the genome sequenced, not just pre-selected positions
- **Gene regions:** full coverage of pharmacogene haplotypes (CYP2D6, HLA), which arrays often miss

Both datasets come from the same individual (Manuel Corpas) and are CC0 licensed. The 23andMe data remains the quickest way to demo the pharmgx-reporter and nutrigx_advisor skills. The WGS data adds depth for variant annotation, structural variant exploration, and comprehensive benchmarking.
