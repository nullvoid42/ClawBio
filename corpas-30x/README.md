# Corpas 30x WGS Reference Genome

## About this genome

This is a fully open personal genome sequenced at 30x depth on Illumina by Dante Labs, aligned to GRCh37. It contains SNPs, indels, structural variants (DEL, DUP, INV, BND, INS), and CNVs with QC metrics (Ti/Tv ~ 2.0, Het/Hom ~ 1.6). Licensed CC0 for unrestricted reuse, it serves as ClawBio's default example genome for demos, tutorials, and regression testing. This dataset is provided for research and educational purposes only; it must not be used for clinical decision-making.

> ClawBio is a research and educational tool. It is not a medical device and does not provide clinical diagnoses. Consult a healthcare professional before making any medical decisions.

## Dataset properties

| Property | Value |
|----------|-------|
| Subject | Manuel Corpas |
| Platform | Illumina (30x coverage) |
| Reference build | GRCh37 (hg19) |
| Sequencing provider | Dante Labs |
| Variant types | SNPs, indels, SVs (DEL, DUP, INV, BND, INS), CNVs |
| QC | Ti/Tv ~ 2.0, Het/Hom ~ 1.6 |
| License | CC0 1.0 Universal (Public Domain) |

## Contents

```
corpas-30x/
  full/                              # Large VCFs (git-ignored, download on demand)
    snps.vcf.gz                      # ~174 MB, genome-wide SNP calls
    indels.vcf.gz                    # ~50 MB, genome-wide indel calls
    sv.pass.vcf.gz                   # ~865 KB, structural variant calls (PASS only)
    cnv.vcf.gz                       # ~95 KB, copy number variant calls

  subsets/                           # Lightweight extracts for tutorials and CI
    chr20_snps_indels.vcf.gz         # Chromosome 20 SNPs + indels
    pgx_loci.vcf.gz                  # 31 pharmacogenomic loci
    nutrigx_loci.vcf.gz              # 30 nutrigenomics loci
    sv_calls.vcf.gz                  # SV calls (copy of full, already small)
    cnv_calls.vcf.gz                 # CNV calls (copy of full, already small)

  baselines/                         # Frozen expected values for regression tests
    qc_summary.json                  # Ti/Tv, Het/Hom, variant counts

  regions/                           # rsID lists for subset extraction
    pgx_rsids.json                   # 31 PGx SNP rsIDs
    nutrigx_rsids.json               # 30 NutriGx SNP rsIDs

  manifest.json                      # Machine-readable file inventory + checksums
  CITATION.cff                       # Machine-readable citation
  README.md                          # This file
  run_benchmark.py                   # CI benchmark runner
```

## How to cite / use

**Citation:** Corpas, M. (2026). Personal Whole Genome Sequencing Variant Calls (SNPs, Indels, SVs, CNVs) of Manuel Corpas from Dante Labs 30x WGS. Zenodo. https://doi.org/10.5281/zenodo.19297389

**All versions:** https://doi.org/10.5281/zenodo.19285820

**License:** CC0 1.0 Universal. You may use this data freely for any purpose without restriction.

**In ClawBio:** This dataset is bundled as the default reference genome. Tutorial commands, demos, and regression tests use its subsets unless you supply your own VCF.

## Data preparation

Full VCFs are too large for git. To download and prepare the data:

```bash
# Download full VCFs from Zenodo (~225 MB)
python scripts/prepare_corpas_30x.py --download

# Generate lightweight subsets (requires bcftools >= 1.17)
python scripts/prepare_corpas_30x.py --subsets

# Compute QC baselines for regression tests
python scripts/prepare_corpas_30x.py --baselines

# Verify checksums
python scripts/prepare_corpas_30x.py --verify

# Or do everything at once
python scripts/prepare_corpas_30x.py --all
```

## See also

- [docs/reference-genome.md](../docs/reference-genome.md): Full documentation with use cases
- [docs/demo-genome.md](../docs/demo-genome.md): The 23andMe Corpasome (SNP chip data)
