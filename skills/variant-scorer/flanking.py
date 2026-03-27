"""Flanking sequence manager with Ensembl REST API fetching.

Provides ±500bp flanking DNA contexts for variant scoring.  On first use,
fetches real flanking sequences from the Ensembl REST API (only public
reference genome coordinates are sent — no patient data).  Fetched
sequences are cached locally in ~/.clawbio/flanking_cache/ for offline reuse.

Falls back to bundled synthetic sequences if offline or API unavailable.
"""

from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

_DATA_DIR = Path(__file__).resolve().parent / "data"
_FLANKING_PATH = _DATA_DIR / "flanking_sequences.json.gz"
_CACHE_DIR = Path.home() / ".clawbio" / "flanking_cache"

# Ensembl REST API
_ENSEMBL_REST = "https://rest.ensembl.org"
_ENSEMBL_GRCH37_REST = "https://grch37.rest.ensembl.org"
_RATE_INTERVAL = 0.07  # ~15 requests/sec max for Ensembl

# Default flanking distance
FLANK = 500

# Cached after first load
_flanking_cache: dict | None = None
_last_request_time: float = 0.0


# ---------------------------------------------------------------------------
# Ensembl API
# ---------------------------------------------------------------------------


def _ensembl_base_url(assembly: str = "GRCh37") -> str:
    """Return the correct Ensembl REST base URL for an assembly."""
    if assembly.upper() in ("GRCH37", "HG19"):
        return _ENSEMBL_GRCH37_REST
    return _ENSEMBL_REST


def build_ensembl_url(
    chrom: str, pos: int, flank: int = FLANK, assembly: str = "GRCh37"
) -> str:
    """Build the Ensembl sequence region URL for a variant's flanking context.

    Args:
        chrom: Chromosome (e.g. "10", "X").
        pos: 1-based genomic position.
        flank: Number of flanking bases on each side.
        assembly: "GRCh37" or "GRCh38".

    Returns:
        Full URL string for the Ensembl REST API.
    """
    start = pos - flank
    end = pos + flank
    base = _ensembl_base_url(assembly)
    return (
        f"{base}/sequence/region/human/"
        f"{chrom}:{start}..{end}:1?content-type=text/plain"
    )


def fetch_flanking_ensembl(
    chrom: str,
    pos: int,
    flank: int = FLANK,
    assembly: str = "GRCh37",
    timeout: int = 15,
) -> str | None:
    """Fetch flanking DNA sequence from the Ensembl REST API.

    Only public reference genome coordinates are sent — no patient data.

    Args:
        chrom: Chromosome.
        pos: 1-based genomic position.
        flank: Flanking bases on each side (default 500 → 1001bp total).
        assembly: Reference assembly ("GRCh37" or "GRCh38").
        timeout: HTTP timeout in seconds.

    Returns:
        DNA sequence string (uppercase ACGT) or None on failure.
    """
    global _last_request_time

    url = build_ensembl_url(chrom, pos, flank, assembly)

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_INTERVAL:
        time.sleep(_RATE_INTERVAL - elapsed)

    req = Request(url)
    req.add_header("User-Agent", "ClawBio/0.1.0")

    try:
        _last_request_time = time.time()
        with urlopen(req, timeout=timeout) as resp:
            seq = resp.read().decode("utf-8").strip()
            if seq and all(c in "ACGTNacgtn" for c in seq):
                return seq.upper()
            return None
    except (URLError, OSError, TimeoutError) as e:
        print(f"    Ensembl API error: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def _cache_path(rsid: str) -> Path:
    """Return the cache file path for an rsid."""
    return _CACHE_DIR / f"{rsid}.json"


def _read_cache(rsid: str) -> dict | None:
    """Read a cached flanking entry, or None if not cached."""
    path = _cache_path(rsid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(rsid: str, entry: dict) -> None:
    """Write a flanking entry to the local cache."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(rsid).write_text(json.dumps(entry))


# ---------------------------------------------------------------------------
# Load or fetch
# ---------------------------------------------------------------------------


def load_or_fetch(
    rsid: str,
    chrom: str,
    pos: int,
    ref: str,
    gene: str,
    assembly: str = "GRCh37",
    offline: bool = False,
) -> dict | None:
    """Get flanking context for a variant: cache → API → bundled fallback.

    Args:
        rsid: Variant rsID.
        chrom: Chromosome.
        pos: 1-based genomic position.
        ref: Reference allele.
        gene: Gene symbol.
        assembly: Reference assembly.
        offline: If True, skip API calls (use cache/bundled only).

    Returns:
        Dict with chrom, pos, ref, gene, context, flank, source — or None.
    """
    # 1. Try local cache
    cached = _read_cache(rsid)
    if cached and "context" in cached and len(cached["context"]) == 2 * FLANK + 1:
        cached["source"] = "cache"
        return cached

    # 2. Try Ensembl API (if online)
    if not offline:
        seq = fetch_flanking_ensembl(chrom, pos, FLANK, assembly)
        if seq and len(seq) == 2 * FLANK + 1:
            entry = {
                "chrom": chrom,
                "pos": pos,
                "ref": ref,
                "gene": gene,
                "context": seq,
                "flank": FLANK,
                "assembly": assembly,
                "source": "ensembl",
            }
            # Use the actual reference genome base at center position
            center_base = seq[FLANK]
            if center_base != ref and ref:
                print(
                    f"    Note: {rsid} panel ref={ref}, genome ref={center_base} "
                    f"(strand difference) — using genome ref",
                    file=sys.stderr,
                )
                entry["ref"] = center_base
            _write_cache(rsid, entry)
            return entry

    # 3. Fall back to bundled synthetic data
    bundled = load_flanking_sequences()
    if rsid in bundled:
        result = dict(bundled[rsid])
        result["source"] = "bundled"
        return result

    return None


def load_or_fetch_panel(
    variants: dict,
    assembly: str = "GRCh37",
    offline: bool = False,
) -> dict:
    """Fetch flanking sequences for all variants in a panel.

    Args:
        variants: {rsid: {chrom, pos, ref, gene}} or {rsid: GenotypeRecord}.
        assembly: Reference assembly.
        offline: Skip API calls.

    Returns:
        Dict mapping rsid -> flanking entry.
    """
    from clawbio.common.parsers import GenotypeRecord

    result = {}
    bundled = load_flanking_sequences()
    total = len(variants)
    fetched_count = 0

    for i, (rsid, info) in enumerate(variants.items(), 1):
        # Extract chrom/pos from either dict or GenotypeRecord
        if isinstance(info, GenotypeRecord):
            chrom = info.chrom
            pos = info.pos
        elif isinstance(info, dict):
            chrom = info.get("chrom", "")
            pos = info.get("pos", 0)
        else:
            continue

        if not chrom or not pos:
            continue

        # Get ref and gene from bundled panel if available
        bundled_info = bundled.get(rsid, {})
        ref = bundled_info.get("ref", "")
        gene = bundled_info.get("gene", rsid)

        entry = load_or_fetch(
            rsid=rsid,
            chrom=chrom,
            pos=pos,
            ref=ref,
            gene=gene,
            assembly=assembly,
            offline=offline,
        )

        if entry:
            result[rsid] = entry
            if entry.get("source") == "ensembl":
                fetched_count += 1
                if fetched_count % 5 == 0 or i == total:
                    print(
                        f"  Fetched {fetched_count} flanking sequences from Ensembl [{i}/{total}]",
                        file=sys.stderr,
                    )

    return result


# ---------------------------------------------------------------------------
# Bundled data (original functions)
# ---------------------------------------------------------------------------


def load_flanking_sequences(path: Path | None = None) -> dict:
    """Load the pre-bundled flanking sequences from disk.

    Args:
        path: Override path to the .json.gz file (for testing).

    Returns:
        Dict mapping rsid -> {chrom, pos, ref, gene, context, flank}.
    """
    global _flanking_cache

    fpath = path or _FLANKING_PATH

    if _flanking_cache is not None and path is None:
        return _flanking_cache

    if not fpath.exists():
        return {}

    with gzip.open(fpath, "rt", encoding="utf-8") as f:
        data = json.load(f)

    if path is None:
        _flanking_cache = data

    return data


def get_context(rsid: str, flanking: dict | None = None) -> str | None:
    """Return the flanking DNA context for a variant.

    Args:
        rsid: The variant rsID (e.g. "rs4244285").
        flanking: Pre-loaded flanking dict (loads from disk if None).

    Returns:
        DNA context string (1001 bp) or None if rsid not in panel.
    """
    if flanking is None:
        flanking = load_flanking_sequences()

    entry = flanking.get(rsid)
    if entry is None:
        return None
    return entry["context"]


def get_variant_info(rsid: str, flanking: dict | None = None) -> dict | None:
    """Return full variant info including flanking context.

    Args:
        rsid: The variant rsID.
        flanking: Pre-loaded flanking dict.

    Returns:
        Dict with chrom, pos, ref, gene, context, flank — or None.
    """
    if flanking is None:
        flanking = load_flanking_sequences()

    return flanking.get(rsid)


def substitute_allele(context: str, alt: str, flank: int = FLANK) -> str:
    """Create an alt-allele version of a flanking context.

    The ref allele sits at position `flank` (center) of the context string.
    This function replaces it with the alt allele.

    Args:
        context: Original DNA context with ref at center.
        alt: Alternate allele to substitute.
        flank: Number of flanking bases on each side.

    Returns:
        Modified context string with alt at center.
    """
    center = flank
    return context[:center] + alt + context[center + 1:]


def panel_rsids() -> set[str]:
    """Return the set of rsIDs in the pre-bundled panel."""
    flanking = load_flanking_sequences()
    return set(flanking.keys())
