"""Tests for the variant-scorer skill.

All tests run without torch/transformers — demo mode and unit tests only.
Integration tests that require the HyenaDNA model are marked with
@pytest.mark.skipif.
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_SKILL_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _SKILL_DIR.parent.parent

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

_FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Imports (after path setup)
# ---------------------------------------------------------------------------
import model as _model
import flanking as _flanking
import variant_scorer as _scorer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def demo_scores():
    """Load the full demo scores JSON."""
    return json.loads(_scorer.DEMO_SCORES_PATH.read_text())


@pytest.fixture
def mini_flanking():
    """Load the mini flanking fixture."""
    return json.loads((_FIXTURES / "mini_flanking.json").read_text())


@pytest.fixture
def mini_scores():
    """Load the mini scores fixture."""
    return json.loads((_FIXTURES / "mini_scores.json").read_text())


# ---------------------------------------------------------------------------
# Demo mode tests
# ---------------------------------------------------------------------------

class TestDemoMode:
    """Tests for demo mode (no torch/transformers required)."""

    def test_demo_scores_exist(self):
        assert _scorer.DEMO_SCORES_PATH.exists()

    def test_demo_scores_structure(self, demo_scores):
        assert "model" in demo_scores
        assert "variants" in demo_scores
        assert isinstance(demo_scores["variants"], list)
        assert len(demo_scores["variants"]) > 0

    def test_demo_variant_fields(self, demo_scores):
        required_fields = {
            "rsid", "gene", "chrom", "pos", "ref", "alt",
            "genotype", "log_likelihood_ref", "log_likelihood_alt",
            "disruption_score", "tier",
        }
        for v in demo_scores["variants"]:
            assert required_fields.issubset(v.keys()), (
                f"Missing fields in {v['rsid']}: "
                f"{required_fields - v.keys()}"
            )

    def test_demo_tiers_valid(self, demo_scores):
        valid_tiers = {"high", "moderate", "low", "benign"}
        for v in demo_scores["variants"]:
            assert v["tier"] in valid_tiers, (
                f"{v['rsid']} has invalid tier: {v['tier']}"
            )

    def test_demo_scores_non_negative(self, demo_scores):
        for v in demo_scores["variants"]:
            assert v["disruption_score"] >= 0, (
                f"{v['rsid']} has negative disruption score"
            )

    def test_demo_report_generation(self, tmp_path, demo_scores):
        """Verify demo mode generates all expected output files."""
        _scorer.generate_report(
            scored_variants=demo_scores["variants"],
            output_dir=tmp_path,
            is_demo=True,
        )

        assert (tmp_path / "report.md").exists()
        assert (tmp_path / "result.json").exists()
        assert (tmp_path / "scores.tsv").exists()
        assert (tmp_path / "reproducibility" / "commands.sh").exists()

    def test_demo_report_content(self, tmp_path, demo_scores):
        """Verify report.md has expected sections."""
        _scorer.generate_report(
            scored_variants=demo_scores["variants"],
            output_dir=tmp_path,
            is_demo=True,
        )

        report = (tmp_path / "report.md").read_text()
        assert "Variant Disruption Score Report" in report
        assert "variant-scorer" in report
        assert "Demo" in report
        assert "Methodology" in report
        assert "Disclaimer" in report

    def test_demo_result_json_envelope(self, tmp_path, demo_scores):
        """Verify result.json follows the standard envelope."""
        _scorer.generate_report(
            scored_variants=demo_scores["variants"],
            output_dir=tmp_path,
            is_demo=True,
        )

        result = json.loads((tmp_path / "result.json").read_text())
        assert result["skill"] == "variant-scorer"
        assert result["version"] == "0.1.0"
        assert "completed_at" in result
        assert "summary" in result
        assert "data" in result
        assert result["summary"]["is_demo"] is True

    def test_demo_scores_tsv(self, tmp_path, demo_scores):
        """Verify scores.tsv has headers and correct row count."""
        _scorer.generate_report(
            scored_variants=demo_scores["variants"],
            output_dir=tmp_path,
            is_demo=True,
        )

        lines = (tmp_path / "scores.tsv").read_text().strip().split("\n")
        assert lines[0].startswith("rsid\t")
        assert len(lines) == len(demo_scores["variants"]) + 1  # header + data


# ---------------------------------------------------------------------------
# Flanking sequence tests
# ---------------------------------------------------------------------------

class TestFlanking:
    """Tests for flanking sequence loading and manipulation."""

    def test_load_flanking_sequences(self):
        """Verify the bundled flanking sequences load correctly."""
        data = _flanking.load_flanking_sequences()
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_flanking_entry_structure(self):
        data = _flanking.load_flanking_sequences()
        for rsid, entry in data.items():
            assert rsid.startswith("rs")
            assert "chrom" in entry
            assert "pos" in entry
            assert "ref" in entry
            assert "gene" in entry
            assert "context" in entry
            assert "flank" in entry
            assert len(entry["context"]) == 2 * entry["flank"] + 1

    def test_get_context(self):
        ctx = _flanking.get_context("rs4244285")
        assert ctx is not None
        assert len(ctx) == 1001

    def test_get_context_missing(self):
        ctx = _flanking.get_context("rs000000000")
        assert ctx is None

    def test_get_variant_info(self):
        info = _flanking.get_variant_info("rs9923231")
        assert info is not None
        assert info["gene"] == "VKORC1"
        assert info["ref"] == "C"

    def test_panel_rsids(self):
        rsids = _flanking.panel_rsids()
        assert isinstance(rsids, set)
        assert "rs4244285" in rsids
        assert "rs9923231" in rsids

    def test_substitute_allele(self):
        # 1001-char context with ref "G" at position 500
        context = "A" * 500 + "G" + "T" * 500
        result = _flanking.substitute_allele(context, "C")
        assert result[500] == "C"
        assert result[:500] == "A" * 500
        assert result[501:] == "T" * 500

    def test_substitute_allele_preserves_length(self):
        context = "ACGTACGT" * 125 + "G"  # 1001 chars
        result = _flanking.substitute_allele(context, "T")
        assert len(result) == len(context)

    def test_mini_flanking_fixture(self, mini_flanking):
        assert "rs4244285" in mini_flanking
        assert "rs9923231" in mini_flanking
        assert mini_flanking["rs4244285"]["gene"] == "CYP2C19"


# ---------------------------------------------------------------------------
# Ensembl API tests
# ---------------------------------------------------------------------------

class TestEnsemblAPI:
    """Tests for Ensembl REST API URL construction and caching."""

    def test_build_url_grch37(self):
        url = _flanking.build_ensembl_url("10", 96541616, flank=500, assembly="GRCh37")
        assert "grch37.rest.ensembl.org" in url
        assert "10:96541116..96542116" in url
        assert "content-type=text/plain" in url

    def test_build_url_grch38(self):
        url = _flanking.build_ensembl_url("10", 96541616, flank=500, assembly="GRCh38")
        assert "rest.ensembl.org" in url
        assert "grch37" not in url

    def test_build_url_coordinates(self):
        """Verify start = pos - flank, end = pos + flank."""
        url = _flanking.build_ensembl_url("1", 1000, flank=100, assembly="GRCh37")
        assert "1:900..1100" in url

    def test_cache_write_and_read(self, tmp_path, monkeypatch):
        """Verify cache round-trip."""
        monkeypatch.setattr(_flanking, "_CACHE_DIR", tmp_path)

        entry = {
            "chrom": "10", "pos": 12345, "ref": "A",
            "gene": "TEST", "context": "A" * 1001, "flank": 500,
        }
        _flanking._write_cache("rs_test_123", entry)
        result = _flanking._read_cache("rs_test_123")

        assert result is not None
        assert result["gene"] == "TEST"
        assert result["context"] == "A" * 1001

    def test_cache_miss(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_flanking, "_CACHE_DIR", tmp_path)
        assert _flanking._read_cache("rs_nonexistent") is None

    def test_load_or_fetch_offline_uses_bundled(self, tmp_path, monkeypatch):
        """In offline mode with empty cache, should fall back to bundled data."""
        monkeypatch.setattr(_flanking, "_CACHE_DIR", tmp_path / "empty_cache")
        result = _flanking.load_or_fetch(
            rsid="rs4244285",
            chrom="10",
            pos=96541616,
            ref="G",
            gene="CYP2C19",
            offline=True,
        )
        assert result is not None
        assert result["source"] == "bundled"
        assert len(result["context"]) == 1001


# ---------------------------------------------------------------------------
# Score interpretation tests
# ---------------------------------------------------------------------------

class TestInterpretScore:
    """Tests for disruption score tier assignment."""

    def test_high_score(self):
        assert _model.interpret_score(2.5) == "high"
        assert _model.interpret_score(2.0) == "high"
        assert _model.interpret_score(10.0) == "high"

    def test_moderate_score(self):
        assert _model.interpret_score(1.5) == "moderate"
        assert _model.interpret_score(1.0) == "moderate"
        assert _model.interpret_score(1.99) == "moderate"

    def test_low_score(self):
        assert _model.interpret_score(0.5) == "low"
        assert _model.interpret_score(0.75) == "low"
        assert _model.interpret_score(0.99) == "low"

    def test_benign_score(self):
        assert _model.interpret_score(0.0) == "benign"
        assert _model.interpret_score(0.1) == "benign"
        assert _model.interpret_score(0.49) == "benign"

    def test_boundary_values(self):
        """Verify exact boundary behaviour."""
        assert _model.interpret_score(0.5) == "low"     # >= 0.5
        assert _model.interpret_score(1.0) == "moderate" # >= 1.0
        assert _model.interpret_score(2.0) == "high"     # >= 2.0

    def test_tier_thresholds_exist(self):
        assert "high" in _model.TIER_THRESHOLDS
        assert "moderate" in _model.TIER_THRESHOLDS
        assert "low" in _model.TIER_THRESHOLDS


# ---------------------------------------------------------------------------
# Determine alt allele tests
# ---------------------------------------------------------------------------

class TestDetermineAlt:
    """Tests for the _determine_alt helper."""

    def test_heterozygous(self):
        assert _scorer._determine_alt("AG", "A") == "G"
        assert _scorer._determine_alt("CT", "C") == "T"

    def test_homozygous_ref(self):
        assert _scorer._determine_alt("CC", "C") == "C"
        assert _scorer._determine_alt("GG", "G") == "G"

    def test_homozygous_alt(self):
        assert _scorer._determine_alt("TT", "C") == "T"

    def test_missing_genotype(self):
        assert _scorer._determine_alt("--", "A") is None
        assert _scorer._determine_alt("00", "A") is None
        assert _scorer._determine_alt("", "A") is None

    def test_indel_markers(self):
        assert _scorer._determine_alt("DD", "A") is None
        assert _scorer._determine_alt("II", "A") is None


# ---------------------------------------------------------------------------
# Dependency check tests
# ---------------------------------------------------------------------------

class TestDependencyCheck:
    """Tests for model dependency checking."""

    def test_check_returns_tuple(self):
        result = _model.check_dependencies()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


# ---------------------------------------------------------------------------
# 23andMe parsing integration
# ---------------------------------------------------------------------------

class TestParseInput:
    """Tests for parsing 23andMe input and matching to panel."""

    def test_parse_demo_patient(self):
        """Verify the pharmgx demo patient file parses and matches panel."""
        from clawbio.common.parsers import parse_genetic_file

        demo_path = (
            _PROJECT_ROOT / "skills" / "pharmgx-reporter" / "demo_patient.txt"
        )
        if not demo_path.exists():
            pytest.skip("Demo patient file not found")

        genotypes = parse_genetic_file(demo_path)
        assert len(genotypes) > 0

        # Check that some variants match the flanking panel
        panel = _flanking.panel_rsids()
        matched = set(genotypes.keys()) & panel
        assert len(matched) > 0, "No demo patient variants matched the panel"

    def test_demo_patient_variant_count(self):
        """Demo patient should have 21 variants, most matching panel."""
        from clawbio.common.parsers import parse_genetic_file

        demo_path = (
            _PROJECT_ROOT / "skills" / "pharmgx-reporter" / "demo_patient.txt"
        )
        if not demo_path.exists():
            pytest.skip("Demo patient file not found")

        genotypes = parse_genetic_file(demo_path)
        panel = _flanking.panel_rsids()
        matched = set(genotypes.keys()) & panel
        assert len(matched) >= 15, (
            f"Expected >= 15 panel matches, got {len(matched)}"
        )
