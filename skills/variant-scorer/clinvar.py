"""ClinVar lookup via NCBI E-utilities API.

Queries ClinVar clinical significance for variants by rsID.
Results are cached locally in ~/.clawbio/clinvar_cache/ for offline reuse.

Only the rsID (public identifier) is sent — no patient data.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_CACHE_DIR = Path.home() / ".clawbio" / "clinvar_cache"
_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_RATE_INTERVAL = 0.5  # Conservative: 2 requests/sec (NCBI allows 3 without API key)
_last_request_time: float = 0.0


def _rate_limit() -> None:
    """Enforce NCBI rate limit."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _RATE_INTERVAL:
        time.sleep(_RATE_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get_json(url: str, timeout: int = 15) -> dict | None:
    """Fetch JSON from a URL with rate limiting."""
    _rate_limit()
    req = Request(url)
    req.add_header("User-Agent", "ClawBio/0.1.0")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        print(f"    ClinVar API error: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(rsid: str) -> Path:
    return _CACHE_DIR / f"{rsid}.json"


def _read_cache(rsid: str) -> dict | None:
    path = _cache_path(rsid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(rsid: str, entry: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(rsid).write_text(json.dumps(entry))


# ---------------------------------------------------------------------------
# ClinVar query
# ---------------------------------------------------------------------------


def lookup_rsid(rsid: str, offline: bool = False) -> dict | None:
    """Look up ClinVar clinical significance for an rsID.

    Args:
        rsid: Variant rsID (e.g. "rs4244285").
        offline: If True, only use cached data.

    Returns:
        Dict with keys: rsid, clinvar_id, clinical_significance, conditions,
        review_status, source — or None if not found.
    """
    # Try cache first
    cached = _read_cache(rsid)
    if cached is not None:
        cached["source"] = "cache"
        return cached

    if offline:
        return None

    # Step 1: Search ClinVar for this rsID
    rs_num = rsid.replace("rs", "")
    search_url = (
        f"{_EUTILS_BASE}/esearch.fcgi?"
        f"db=clinvar&term=rs{rs_num}&retmode=json&retmax=5"
    )
    search_result = _get_json(search_url)
    if not search_result:
        return None

    id_list = search_result.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        # No ClinVar entry — cache the miss
        entry = {
            "rsid": rsid,
            "clinvar_id": None,
            "clinical_significance": "not in ClinVar",
            "conditions": [],
            "review_status": "",
        }
        _write_cache(rsid, entry)
        return {**entry, "source": "api"}

    # Step 2: Get summary for the first ClinVar ID
    uid = id_list[0]
    summary_url = (
        f"{_EUTILS_BASE}/esummary.fcgi?"
        f"db=clinvar&id={uid}&retmode=json"
    )
    summary_result = _get_json(summary_url)
    if not summary_result:
        return None

    doc = summary_result.get("result", {}).get(str(uid), {})

    # Extract clinical significance from germline_classification
    # (ClinVar API v2 uses germline_classification instead of clinical_significance)
    germ = doc.get("germline_classification", {})
    if isinstance(germ, dict):
        significance = germ.get("description", "")
    else:
        significance = ""

    # Fallback to older API field names
    if not significance:
        clin_sig = doc.get("clinical_significance", {})
        if isinstance(clin_sig, dict):
            significance = clin_sig.get("description", "")
        elif isinstance(clin_sig, str):
            significance = clin_sig

    if not significance:
        significance = "unknown"

    # Extract conditions/traits from germline_classification.trait_set
    conditions = []
    trait_set = (
        germ.get("trait_set", []) if isinstance(germ, dict)
        else doc.get("trait_set", [])
    )
    if isinstance(trait_set, list):
        for trait in trait_set:
            if isinstance(trait, dict):
                name = trait.get("trait_name", "")
                if name:
                    conditions.append(name)

    # Extract review status
    review = ""
    if isinstance(germ, dict):
        review = germ.get("review_status", "")

    # Extract title (often has the HGVS notation)
    title = doc.get("title", "")

    entry = {
        "rsid": rsid,
        "clinvar_id": uid,
        "clinical_significance": significance,
        "conditions": conditions,
        "review_status": review,
        "title": title,
    }

    _write_cache(rsid, entry)
    return {**entry, "source": "api"}


def lookup_batch(
    rsids: list[str],
    offline: bool = False,
) -> dict[str, dict]:
    """Look up ClinVar data for multiple rsIDs.

    Args:
        rsids: List of rsIDs to look up.
        offline: Skip API calls.

    Returns:
        Dict mapping rsid -> ClinVar result dict.
    """
    results = {}
    total = len(rsids)
    fetched = 0

    for i, rsid in enumerate(rsids, 1):
        result = lookup_rsid(rsid, offline=offline)
        if result:
            results[rsid] = result
            if result.get("source") == "api":
                fetched += 1
                if fetched % 5 == 0 or i == total:
                    print(
                        f"  Queried ClinVar for {fetched} variants [{i}/{total}]",
                        file=sys.stderr,
                    )

    return results


def significance_to_category(significance: str) -> str:
    """Map ClinVar significance string to a simple category.

    Returns one of: "pathogenic", "likely_pathogenic", "vus",
    "likely_benign", "benign", "other", "not_in_clinvar".
    """
    sig = significance.lower().strip()

    if "not in clinvar" in sig:
        return "not_in_clinvar"
    if "pathogenic" in sig and "likely" not in sig and "conflict" not in sig:
        return "pathogenic"
    if "likely pathogenic" in sig:
        return "likely_pathogenic"
    if "benign" in sig and "likely" not in sig and "conflict" not in sig:
        return "benign"
    if "likely benign" in sig:
        return "likely_benign"
    if "uncertain" in sig or "vus" in sig:
        return "vus"
    return "other"
