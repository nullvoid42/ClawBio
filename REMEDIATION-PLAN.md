# ClawBio Remediation Plan

Response to the clawbio_bench external audit by Sergey Kornilov (Biostochastics, LLC).

| | |
|---|---|
| **Audit date** | 2026-04-05 |
| **Audit commit** | `1481fb4` |
| **Benchmark** | [biostochastics/clawbio_bench](https://github.com/biostochastics/clawbio_bench) v0.1.0 |
| **Result** | 80/140 tests passing (57.1%) |
| **This plan** | 13 tasks, 3 priority tiers |

## Executive Summary

An independent benchmark suite tested 7 ClawBio skills across three dimensions: safety (does it reject unsafe inputs?), correctness (does it produce the right numerical answer?), and honesty (does it report what it actually did?). Two skills failed critically: equity-scorer (20% pass rate) and fine-mapping (25% pass rate). PharmGx is at 42%. The remaining skills are above 75%.

The most serious finding is a scientific honesty failure: equity-scorer computes Nei's GST but labels the output as Hudson FST. This is not a bug; it is a mislabeling of the statistical method used.

## Scorecard

| Skill | Pass | Fail | Rate | Worst Finding |
|-------|------|------|------|---------------|
| bio-orchestrator | 41 | 13 | 75.9% | stub_silent, routed_wrong |
| equity-scorer | 3 | 12 | 20.0% | fst_mislabeled, heim_unbounded, edge_crash |
| nutrigx-advisor | 8 | 2 | 80.0% | snp_invalid, score_incorrect |
| pharmgx-reporter | 14 | 19 | 42.4% | correct_determinate, disclosure_failure |
| claw-metagenomics | 6 | 1 | 85.7% | exit_suppressed |
| fine-mapping | 4 | 12 | 25.0% | pathology_flagged |
| clinical-variant | 4 | 1 | 80.0% | report_structure_complete |

---

## P0: Fix Before Next Release

These findings undermine scientific credibility. No release should ship until they are resolved.

### 1. Equity-scorer: FST mislabeling (honesty failure)

**Finding:** C-06 `fst_mislabeled`. Output computes Nei's GST (1973) but the report header and JSON field say "Hudson FST".

**File:** `skills/equity-scorer/equity_scorer.py` (1,125 lines)

**Fix options:**
- (a) Rename output to "Nei's GST" throughout (report header, JSON key, CSV column). One-liner.
- (b) Implement actual Hudson FST (1992). Requires rewriting the FST calculation to use the Hudson, Slatkin, Maddison formula. More work but more correct.

**Recommended:** Option (a) first (fixes the honesty failure immediately), then option (b) as a follow-up (adds the correct statistic).

**References:**
- Nei, M. (1973). Analysis of Gene Diversity in Subdivided Populations. PNAS, 70(12), 3321-3323.
- Hudson, R.R., Slatkin, M. & Maddison, W.P. (1992). Estimation of levels of gene flow from DNA sequence data. Genetics, 132(2), 583-589.

**Tests:** Update `skills/equity-scorer/tests/test_equity_scorer.py` (254 lines). Add test that asserts output label matches the formula used.

**Verification:** `clawbio-bench --smoke --harness equity_scorer --repo .`

---

### 2. Equity-scorer: HEIM unbounded with custom weights

**Finding:** U-2/F-27 `heim_unbounded`. Passing `--weights 1,1,1,1` produces HEIM scores exceeding 100 (observed: ~330). The HEIM score is defined on [0, 100] but there is no validation or normalization of user-supplied weights.

**File:** `skills/equity-scorer/equity_scorer.py`

**Fix:**
1. Validate that weights sum to 1.0 (or normalize them if they do not)
2. Clamp final HEIM score to [0, 100]
3. Raise `ValueError` if any weight is negative

**Tests:** Add `test_heim_custom_weights_bounded`, `test_heim_negative_weight_rejected`, `test_heim_weights_normalized`.

---

### 3. Equity-scorer: 9 edge case crashes

**Finding:** `edge_crash` on 9 of 15 tests. The tool crashes with traceback (exit code 1) instead of producing output or a clean error on edge case inputs.

**File:** `skills/equity-scorer/equity_scorer.py`

**Failing inputs and required fixes:**

| Test Case | Input | Required Behaviour |
|-----------|-------|--------------------|
| eq_01_fst_known_af | 2 pops, known allele frequencies | Compute FST, do not crash |
| eq_02_fst_identical | Identical populations | Return FST = 0 |
| eq_03_fst_monomorphic | Monomorphic sites | Return NaN FST without crash |
| eq_04_fst_single_sample | n=1 per population | Warn unreliable, do not crash |
| eq_05_het_all_het | All heterozygous genotypes | Produce bounded HEIM |
| eq_06_het_all_hom | All homozygous genotypes | Produce bounded HEIM |
| eq_07_heim_balanced | 5-superpop balanced dataset | Produce high HEIM |
| eq_08_heim_single_pop | Single population | Produce low HEIM, stay bounded |
| eq_11_multiallelic | Multiallelic sites (AF > 1.0 naive) | Handle without crash |

**Approach:** Add input validation at the top of the computation pipeline. Each edge case needs a guard: check for division by zero (monomorphic), check sample size (n=1), check allele count (multiallelic), check population count (single pop). Return NaN or a warning instead of crashing.

**Tests:** One test per edge case. All 9 must pass.

---

### 4. Fine-mapping: SuSiE null component (architectural)

**Finding:** `pathology_flagged`. The SuSiE implementation has no null component in each single-effect regression. Null loci (z=0 everywhere) return phantom PIPs (~0.70 per variant). Single-causal loci produce spurious secondary signals. The reference implementation (`susieR`) mitigates this via `null_weight` and `susie_get_cs` purity pruning.

**File:** `skills/fine-mapping/core/susie.py` (4,834 lines)

**Fix:** This is the most complex fix in the plan. Two changes needed:
1. Add a `null_weight` parameter to the single-effect regression. When fitting each effect, include a null hypothesis (no effect at any variant). This prevents the model from forcing all posterior mass onto variants when the true signal is zero.
2. Add `susie_get_cs`-style purity pruning to the credible set extraction. After fitting, compute purity for each credible set and discard sets below a minimum purity threshold (default 0.5 in susieR).

**Reference implementation:** `susieR` R package, specifically `susie()` function's `null_weight` parameter and `susie_get_cs()` function.

**Tests:** Add `test_null_locus_no_phantom_pip` (z=0 locus should produce PIPs near 0), `test_single_causal_no_spurious_secondary` (one causal variant should produce one credible set).

---

### 5. Fine-mapping: PIP calculation (alpha vs true PIP)

**Finding:** The credible set `pip` field stores single-effect alpha rather than true PIP. True PIP formula: `PIP_j = 1 - product(1 - alpha_lj)` across all L effects.

**File:** `skills/fine-mapping/core/susie.py` or `skills/fine-mapping/core/credible_sets.py`

**Fix:** Replace the PIP assignment with the correct aggregation formula. This is a one-liner once you find where PIPs are extracted from the fitted model.

**Tests:** Add `test_pip_aggregation_formula` with a known two-effect example where true PIP differs from single-effect alpha.

---

### 6. Fine-mapping: purity mean vs min

**Finding:** Purity computed as mean pairwise |r| instead of minimum pairwise |r| per Wang et al. 2020 section 3.2. This promotes credible sets that span independent LD blocks (false positives).

**File:** `skills/fine-mapping/core/credible_sets.py` (or wherever purity is computed)

**Fix:** Change `mean()` to `min()` in the purity calculation. One-liner.

**Also fix:** `mu` stores alpha-weighted mixture contributions rather than conditional posterior means (contrary to docstring and Wang eq. 4).

**Reference:** Wang, G., Sarkar, A., Carbonetto, P. & Stephens, M. (2020). A simple new approach to variable selection in regression, with application to genetic fine-mapping. JRSS-B.

**Tests:** Add `test_purity_is_minimum_r` with a credible set spanning two LD blocks where mean > 0.5 but min < 0.5 (should be pruned).

---

### 7. Fine-mapping: input validation

**Finding:** Zero or negative SE, zero or negative n, and coverage=0 all produce numeric output without raising. Reference implementations reject these at validation time.

**File:** `skills/fine-mapping/fine_mapping.py` (394 lines) and/or `skills/fine-mapping/core/io.py`

**Fix:** Add `ValueError` contracts at input parsing:
```python
if (se <= 0).any():
    raise ValueError("Standard errors must be positive")
if n <= 0:
    raise ValueError("Sample size must be positive")
if coverage <= 0 or coverage > 1:
    raise ValueError("Coverage must be in (0, 1]")
```

**Tests:** `test_negative_se_raises`, `test_zero_n_raises`, `test_zero_coverage_raises`.

---

## P1: Fix Within 2 Weeks

These findings degrade trust but do not block the release.

### 8. PharmGx-reporter: CPIC compliance gaps (19 failures)

**Finding:** 42.4% pass rate (14/33). Finding categories: `correct_determinate` and `disclosure_failure`. The specific per-test failures have been requested from Sergey.

**File:** `skills/pharmgx-reporter/pharmgx_reporter.py` (2,002 lines)

**Action:**
1. Wait for Sergey's per-test breakdown (requested in reply email)
2. Triage each of the 19 failures against CPIC guidelines
3. Fix classification logic for each failing drug-gene pair
4. Update tests in `skills/pharmgx-reporter/tests/test_pharmgx.py` (529 lines)

**Blocked by:** Sergey's detailed PGx findings.

---

### 9. Bio-orchestrator: stub_silent + routing collision

**Finding:** 8 `stub_silent` findings (routes correctly to stub skills but emits no warning that the skill has no executable). 1 `routed_wrong` (kw_14: "variant+diversity" routes to equity-scorer instead of vcf-annotator). 1 `unroutable_crash` (inj_03: flock routing hijack crashes).

**File:** `skills/bio-orchestrator/orchestrator.py` (662 lines)

**Fixes:**
1. **stub_silent:** When the orchestrator routes to a skill that has no Python script (SKILL.md only), emit a warning: "Note: [skill-name] is a SKILL.md-only stub. No executable is available. Applying methodology from SKILL.md."
2. **routed_wrong:** Fix keyword priority in the KEYWORD_MAP. "variant" should prioritise vcf-annotator over equity-scorer when "diversity" is not also present. Add disambiguation logic.
3. **unroutable_crash:** The flock routing hijack test (inj_03) crashes instead of gracefully handling the injection attempt. Add input sanitization.

**Tests:** Update `skills/bio-orchestrator/tests/test_orchestrator.py` (237 lines). Add `test_stub_routing_warns`, `test_variant_keyword_routes_to_vcf_annotator`, `test_injection_handled_gracefully`.

---

### 10. NutriGx-advisor: empty input crash + allele mismatch

**Finding:** (1) `snp_invalid` (ng_07): Empty input crashes instead of clean error. (2) `score_incorrect` (ng_09): Hom-ref genotypes (all reference alleles) are treated as `allele_mismatch` and return "Unknown" instead of score 0.

**Files:**
- `skills/nutrigx_advisor/nutrigx_advisor.py` (161 lines)
- `skills/nutrigx_advisor/parse_input.py`
- `skills/nutrigx_advisor/score_variants.py`

**Fixes:**
1. Add empty file check in `parse_input.py`: if no variants parsed, raise a clean error with message "No variants found in input file"
2. Fix genotype classification in `score_variants.py`: hom-ref (e.g. AA when ref=A) should score 0, not be classified as allele_mismatch

**Tests:** Update `skills/nutrigx_advisor/tests/test_nutrigx.py` (123 lines). Add `test_empty_input_error`, `test_homref_scores_zero`.

---

## P2: Infrastructure

### 11. Metagenomics: exit code suppression

**Finding:** `exit_suppressed`. Shell exit codes from metagenomics tool calls are suppressed to warnings (`critical=False`). Non-zero exit codes should propagate as errors.

**File:** `skills/claw-metagenomics/` (identify the subprocess call that swallows exit codes)

**Fix:** Change `critical=False` to `critical=True` or propagate `subprocess.CalledProcessError`.

---

### 12. Integrate clawbio_bench into CI

**Action:** Add Sergey's `audit.yml` GitHub Actions workflow to ClawBio's CI pipeline.

**Workflow:**
1. On every PR, run `clawbio-bench --smoke --repo .` against the PR branch
2. Exit 1 (findings exist): post advisory comment on PR with new vs resolved findings
3. Exit >= 2 (infrastructure failure): block merge until audit infrastructure is repaired
4. Upload verdict tree as artifact (30-day retention)

**Implementation:**
- Add `.github/workflows/audit.yml` referencing `biostochastics/clawbio_bench`
- Pin to a specific bench version tag for reproducibility
- Run after existing CI tests pass (to avoid wasting bench time on broken code)

---

### 13. Contribute to biostochastics/clawbio_bench

**Action:** Manuel to join as collaborator on the benchmark repo.

**Contributions planned:**
- Add test cases for skills not yet covered (gwas-prs, gwas-lookup, genome-compare, profile-report)
- Verify fixes pass the harness as we ship them
- Add SKILL.md conformance checks to the bench (complement the pr-audit)

**GitHub handle:** `manuelcorpas` (collaborator access requested from Sergey)

---

## Verification Protocol

For every fix in this plan:

1. **Write the clawbio_bench test first** (or confirm Sergey's test covers the fix)
2. **Run the specific harness** to confirm the test fails (red)
3. **Implement the fix** following red/green TDD
4. **Run the harness again** to confirm the test passes (green)
5. **Run the full smoke suite** to confirm no regressions: `clawbio-bench --smoke --repo .`
6. **Update the skill's SKILL.md Gotchas section** with the failure mode that was fixed
7. **Commit with reference** to the bench finding ID (e.g. "fix: C-06 FST mislabeling")

Target: raise the overall pass rate from 57.1% to >90% within 2 weeks.

---

## Timeline

| Week | Target | Expected Pass Rate |
|------|--------|--------------------|
| Week 1 | P0 complete (equity-scorer + fine-mapping) | ~75% |
| Week 2 | P1 complete (pharmgx + orchestrator + nutrigx) | ~90% |
| Week 3 | P2 complete (CI integration, metagenomics, bench contributions) | >90% |

## Contact

- **Benchmark maintainer:** Sergey Kornilov, sergey.kornilov@biostochastics.com
- **ClawBio lead:** Manuel Corpas, m.corpas@westminster.ac.uk
- **Benchmark repo:** https://github.com/biostochastics/clawbio_bench
