# Region Definitions

This directory contains rsID lists used by `scripts/prepare_corpas_30x.py` to extract
skill-specific VCF subsets from the full 30x WGS data.

## Files

- **pgx_rsids.json**: 31 pharmacogenomic SNPs (12 genes, CPIC guidelines). Source: `skills/pharmgx-reporter/pharmgx_reporter.py`.
- **nutrigx_rsids.json**: 30 nutrigenomics SNPs (13 dietary domains). Source: `skills/nutrigx_advisor/data/snp_panel.json`.

## How subsets are generated

The preparation script (`scripts/prepare_corpas_30x.py --subsets`) reads each rsID list, searches the full VCF for matching variant IDs, and extracts them into a compressed VCF subset. This approach avoids the need for GRCh37 coordinate BED files, since the rsIDs are resolved directly against the VCF's ID column.

If an rsID is missing from the VCF (not called by the variant caller, or absent from the sequenced genome), the script logs a warning with the missing rsID and the total coverage count. This is expected for some loci, particularly rare variants that may be reference-homozygous in this individual.

## Updating

When new SNPs are added to the pharmgx-reporter or nutrigx_advisor panels, update the corresponding JSON file here and re-run `python scripts/prepare_corpas_30x.py --subsets`.
