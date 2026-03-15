#!/usr/bin/env python3
"""ClawBio scRNA Orchestrator.

Scanpy-based single-cell RNA-seq pipeline:
QC/filtering -> optional doublet detection -> normalisation/log1p ->
optional CellTypist annotation -> HVG -> PCA/neighbors/UMAP ->
Leiden clustering -> marker detection.

Usage:
    python scrna_orchestrator.py --input sample.h5ad --output report_dir
    python scrna_orchestrator.py --input filtered_feature_bc_matrix --output report_dir
    python scrna_orchestrator.py --demo --output demo_report
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from clawbio.common.checksums import sha256_file
from clawbio.common.report import (
    generate_report_footer,
    generate_report_header,
    write_result_json,
)
from clawbio.common.scrna_io import (
    compute_input_checksum,
    detect_processed_input_reason as shared_detect_processed_input_reason,
    load_10x_mtx_data,
    resolve_input_source,
)

DISCLAIMER = (
    "ClawBio is a research and educational tool. It is not a medical device "
    "and does not provide clinical diagnoses. Consult a healthcare professional "
    "before making any medical decisions."
)
DEMO_SOURCE_ENV = "CLAWBIO_SCRNA_DEMO_SOURCE"
DEFAULT_CELLTYPIST_MODEL = "Immune_All_Low"
EMBEDDING_ARTIFACT_KEY = "clawbio_scrna_embedding"
DEFAULT_LATENT_REP = "X_scvi"
DEFAULT_COUNTS_LAYER = "counts"


def _import_scanpy():
    """Import scanpy lazily with a clear user-facing error."""
    try:
        import scanpy as sc  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "scanpy is required for scrna-orchestrator. "
            "Install it with: pip install scanpy anndata"
        ) from exc
    return sc


def _import_scrublet():
    """Import scrublet for optional doublet detection."""
    try:
        import scrublet  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "scrublet is required for --doublet-method scrublet. "
            "Install it with: pip install scrublet"
        ) from exc


def _import_celltypist():
    """Import celltypist for optional annotation."""
    try:
        import celltypist  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "celltypist is required for --annotate celltypist. "
            "Install it with: pip install celltypist"
        ) from exc
    return celltypist


def build_demo_adata(random_state: int):
    """Create deterministic synthetic AnnData demo data."""
    from anndata import AnnData  # type: ignore

    rng = np.random.default_rng(random_state)
    n_cells = 240
    n_genes = 480
    n_clusters = 3
    cells_per_cluster = n_cells // n_clusters

    base_profiles = []
    for i in range(n_clusters):
        base = rng.gamma(shape=2.0, scale=1.2, size=n_genes)
        marker_start = 40 + i * 20
        marker_end = marker_start + 15
        base[marker_start:marker_end] += 6.0
        base_profiles.append(base)

    expr_blocks = []
    labels = []
    for i, base in enumerate(base_profiles):
        lam = np.clip(base, 0.05, None)
        counts = rng.poisson(lam=lam, size=(cells_per_cluster, n_genes))
        libsize_scale = rng.lognormal(mean=0.0, sigma=0.35, size=(cells_per_cluster, 1))
        counts = np.round(counts * libsize_scale).astype(np.int32)
        expr_blocks.append(counts)
        labels.extend([f"cluster_{i}"] * cells_per_cluster)

    x = np.vstack(expr_blocks)
    gene_names = [f"Gene{i:03d}" for i in range(n_genes)]
    for i in range(20):
        gene_names[i] = f"MT-GENE{i:02d}"

    obs = pd.DataFrame(
        {
            "sample_id": [f"cell_{i:03d}" for i in range(n_cells)],
            "demo_truth": labels,
        },
        index=[f"cell_{i:03d}" for i in range(n_cells)],
    )
    var = pd.DataFrame(index=gene_names)
    return AnnData(X=x, obs=obs, var=var)


def load_demo_adata(random_state: int, demo_source_policy: str | None = None):
    """Load real PBMC3k demo data, falling back to synthetic data when needed."""
    sc = _import_scanpy()
    policy = (demo_source_policy or os.getenv(DEMO_SOURCE_ENV, "auto")).strip().lower()
    if policy not in {"auto", "pbmc3k", "synthetic"}:
        policy = "auto"

    if policy == "synthetic":
        return build_demo_adata(random_state), "synthetic_forced"

    try:
        adata = sc.datasets.pbmc3k()
        adata.var_names_make_unique()
        sc.pp.filter_cells(adata, min_counts=1)
        if adata.n_obs == 0:
            raise ValueError("PBMC3k demo had no cells after filtering min_counts=1.")
        return adata, "pbmc3k_raw"
    except Exception as exc:
        print(
            f"WARNING: Failed to load PBMC3k demo ({exc}); falling back to synthetic demo data.",
            file=sys.stderr,
        )
        return build_demo_adata(random_state), "synthetic_fallback"


def detect_processed_input_reason(adata) -> str | None:
    """Detect whether input looks preprocessed (log-normalized/scaled) instead of raw counts."""
    return shared_detect_processed_input_reason(
        adata,
        expected_input="raw-count .h5ad or 10x single-cell input",
    )


def _copy_matrix(matrix):
    """Copy dense/sparse matrices when possible."""
    return matrix.copy() if hasattr(matrix, "copy") else matrix


def get_embedding_contract(adata) -> dict[str, str]:
    """Return the downstream latent artifact contract when present."""
    contract: dict[str, str] = {}
    raw_contract = adata.uns.get(EMBEDDING_ARTIFACT_KEY, {})
    if isinstance(raw_contract, dict):
        contract = {str(key): str(value) for key, value in raw_contract.items() if isinstance(key, str)}

    rep_key = contract.get("preferred_rep", DEFAULT_LATENT_REP)
    counts_layer = contract.get("counts_layer", DEFAULT_COUNTS_LAYER)
    if rep_key in getattr(adata, "obsm", {}):
        contract["preferred_rep"] = rep_key
    if counts_layer in getattr(adata, "layers", {}):
        contract["counts_layer"] = counts_layer

    if "preferred_rep" not in contract and DEFAULT_LATENT_REP in getattr(adata, "obsm", {}):
        contract["preferred_rep"] = DEFAULT_LATENT_REP
    if "counts_layer" not in contract and DEFAULT_COUNTS_LAYER in getattr(adata, "layers", {}):
        contract["counts_layer"] = DEFAULT_COUNTS_LAYER

    return contract


def _resolve_requested_rep(adata, use_rep: str) -> str:
    """Resolve CLI use-rep mode into an obsm key or empty string."""
    normalized = (use_rep or "auto").strip()
    if not normalized or normalized == "auto":
        contract = get_embedding_contract(adata)
        rep_key = contract.get("preferred_rep", "")
        return rep_key if rep_key in getattr(adata, "obsm", {}) else ""
    if normalized == "none":
        return ""
    if normalized not in getattr(adata, "obsm", {}):
        raise ValueError(f"Requested latent representation not found in adata.obsm: {normalized}")
    return normalized


def _recover_counts_source(adata, *, expected_input: str) -> tuple[Any, str]:
    """Return an AnnData view with raw counts restored to X when possible."""
    counts_layer = get_embedding_contract(adata).get("counts_layer", "")
    counts_adata = adata.copy()
    if counts_layer:
        counts_adata.X = _copy_matrix(adata.layers[counts_layer])
        processed_reason = shared_detect_processed_input_reason(
            counts_adata,
            expected_input=expected_input,
            layer=counts_layer,
        )
        if processed_reason:
            raise ValueError(processed_reason)
        return counts_adata, counts_layer

    processed_reason = shared_detect_processed_input_reason(
        counts_adata,
        expected_input=expected_input,
    )
    if processed_reason:
        raise ValueError(
            "Detected a latent embedding artifact but could not recover raw counts. "
            f"{processed_reason}"
        )
    return counts_adata, ""


def resolve_contrast_request(args: argparse.Namespace) -> tuple[dict[str, str] | None, dict[str, Any]]:
    """Resolve canonical + deprecated contrast-analysis flags."""
    def _pick(primary: Any, legacy: Any, *, primary_name: str, legacy_name: str) -> Any:
        if primary is not None and legacy is not None and primary != legacy:
            raise ValueError(
                f"Conflicting values for {primary_name} and deprecated {legacy_name}. "
                f"Use only {primary_name}."
            )
        return primary if primary is not None else legacy

    groupby = _pick(
        getattr(args, "contrast_groupby", None),
        getattr(args, "de_groupby", None),
        primary_name="--contrast-groupby",
        legacy_name="--de-groupby",
    )
    group1 = _pick(
        getattr(args, "contrast_group1", None),
        getattr(args, "de_group1", None),
        primary_name="--contrast-group1",
        legacy_name="--de-group1",
    )
    group2 = _pick(
        getattr(args, "contrast_group2", None),
        getattr(args, "de_group2", None),
        primary_name="--contrast-group2",
        legacy_name="--de-group2",
    )
    top_genes = _pick(
        getattr(args, "contrast_top_genes", None),
        getattr(args, "de_top_genes", None),
        primary_name="--contrast-top-genes",
        legacy_name="--de-top-genes",
    )
    volcano = bool(getattr(args, "contrast_volcano", False) or getattr(args, "de_volcano", False))
    used_legacy_flags = any(
        getattr(args, attr, None) is not None
        for attr in ("de_groupby", "de_group1", "de_group2", "de_top_genes")
    ) or bool(getattr(args, "de_volcano", False))

    if top_genes is None:
        top_genes = 50

    provided = {
        "--contrast-groupby": groupby,
        "--contrast-group1": group1,
        "--contrast-group2": group2,
    }
    provided_count = sum(1 for value in provided.values() if value)
    if provided_count == 0:
        if volcano:
            raise ValueError(
                "--contrast-volcano requires --contrast-groupby, --contrast-group1, and --contrast-group2."
            )
        return None, {"top_genes": int(top_genes), "volcano": volcano, "used_legacy_flags": used_legacy_flags}
    if provided_count != 3:
        missing = [flag for flag, value in provided.items() if not value]
        raise ValueError(
            "Contrastive marker analysis requires --contrast-groupby, "
            "--contrast-group1, and --contrast-group2 together. "
            f"Missing: {', '.join(missing)}."
        )
    if int(top_genes) < 1:
        raise ValueError("--contrast-top-genes must be >= 1.")

    request = {
        "groupby": str(groupby),
        "group1": str(group1),
        "group2": str(group2),
    }
    extra = {
        "top_genes": int(top_genes),
        "volcano": volcano,
        "used_legacy_flags": used_legacy_flags,
    }
    return request, extra


def _cluster_sort_key(cluster: str) -> tuple[int, Any]:
    """Sort cluster labels numerically when possible."""
    try:
        return (0, int(cluster))
    except ValueError:
        return (1, cluster)


def _normalize_celltypist_model_name(model_name: str) -> str:
    """Normalize a CellTypist model identifier to a .pkl filename when needed."""
    normalized = model_name.strip()
    if not normalized:
        return f"{DEFAULT_CELLTYPIST_MODEL}.pkl"
    if "/" not in normalized and "\\" not in normalized and not normalized.endswith(".pkl"):
        normalized = f"{normalized}.pkl"
    return normalized


def resolve_celltypist_model_path(celltypist, model_name: str) -> Path:
    """Resolve a CellTypist model to a local path without triggering downloads."""
    normalized = _normalize_celltypist_model_name(model_name)
    if "/" in normalized or "\\" in normalized:
        path = Path(normalized).expanduser()
    else:
        path = Path(celltypist.models.models_path) / normalized

    if path.exists():
        return path

    raise RuntimeError(
        "CellTypist model "
        f"'{normalized}' was not found locally at {path}. "
        "Install it first with: "
        f"python -c \"import celltypist; celltypist.models.download_models(model='{normalized}')\". "
        "Runtime downloads are disabled for this skill."
    )


def load_data(
    input_path: str | None,
    demo: bool,
    random_state: int,
    *,
    use_rep: str = "auto",
):
    """Load AnnData from supported input and resolve latent downstream mode."""
    sc = _import_scanpy()

    if demo:
        adata, demo_source = load_demo_adata(random_state)
        return adata, None, True, demo_source, None, {
            "requested_use_rep": use_rep,
            "resolved_use_rep": "",
            "counts_layer": "",
            "graph_basis": "pca",
        }

    if not input_path:
        raise ValueError("Provide --input <input.h5ad|matrix.mtx|10x_dir> or --demo.")

    source_info = resolve_input_source(Path(input_path))
    if source_info["format"] == "10x_mtx":
        if (use_rep or "auto").strip() not in {"", "auto", "none"}:
            raise ValueError("--use-rep is only supported for `.h5ad` input.")
        adata = load_10x_mtx_data(source_info)
        processed_reason = shared_detect_processed_input_reason(
            adata,
            expected_input="raw-count .h5ad or 10x single-cell input",
        )
        if processed_reason:
            raise ValueError(processed_reason)
        source_info["selected_layer"] = ""
        source_info["resolved_use_rep"] = ""
        source_info["counts_layer"] = ""
        return adata, Path(input_path), False, None, source_info, {
            "requested_use_rep": use_rep,
            "resolved_use_rep": "",
            "counts_layer": "",
            "graph_basis": "pca",
        }

    adata_loaded = sc.read_h5ad(Path(source_info["input_path"]))
    resolved_use_rep = _resolve_requested_rep(adata_loaded, use_rep)
    counts_adata, counts_layer = _recover_counts_source(
        adata_loaded,
        expected_input="raw-count .h5ad or 10x single-cell input",
    )

    if resolved_use_rep and resolved_use_rep not in counts_adata.obsm:
        raise ValueError(
            f"Requested latent representation was not preserved after counts recovery: {resolved_use_rep}"
        )

    source_info["selected_layer"] = counts_layer
    source_info["resolved_use_rep"] = resolved_use_rep
    source_info["counts_layer"] = counts_layer
    return counts_adata, Path(input_path), False, None, source_info, {
        "requested_use_rep": use_rep,
        "resolved_use_rep": resolved_use_rep,
        "counts_layer": counts_layer,
        "graph_basis": resolved_use_rep or "pca",
    }


def qc_filter(
    adata,
    min_genes: int,
    min_cells: int,
    max_mt_pct: float,
) -> tuple[Any, dict[str, int]]:
    """Compute QC metrics and apply filtering."""
    sc = _import_scanpy()

    adata = adata.copy()
    adata.var_names_make_unique()
    adata.var["mt"] = adata.var_names.str.upper().str.startswith("MT-")
    sc.pp.calculate_qc_metrics(
        adata,
        qc_vars=["mt"],
        percent_top=None,
        log1p=False,
        inplace=True,
    )

    stats = {
        "n_cells_before": int(adata.n_obs),
        "n_genes_before": int(adata.n_vars),
    }

    adata = adata[adata.obs["n_genes_by_counts"] >= min_genes, :].copy()
    adata = adata[adata.obs["pct_counts_mt"] <= max_mt_pct, :].copy()
    sc.pp.filter_genes(adata, min_cells=min_cells)

    stats["n_cells_after"] = int(adata.n_obs)
    stats["n_genes_after"] = int(adata.n_vars)

    if adata.n_obs == 0:
        raise ValueError("Filtering removed all cells. Adjust QC thresholds.")
    if adata.n_vars == 0:
        raise ValueError("Filtering removed all genes. Adjust QC thresholds.")

    return adata, stats


def run_doublet_detection(
    adata,
    method: str,
    random_state: int,
) -> tuple[Any, dict[str, Any] | None]:
    """Run optional doublet detection on QC-filtered raw counts."""
    if method == "none":
        return adata.copy(), None

    _import_scrublet()
    sc = _import_scanpy()

    adata = adata.copy()
    obs_before = adata.obs.copy()
    n_prin_comps = min(30, adata.n_obs - 1, adata.n_vars - 1)
    if n_prin_comps < 2:
        raise ValueError(
            "Scrublet requires at least 3 cells and 3 genes after QC filtering. "
            f"Got n_obs={adata.n_obs}, n_vars={adata.n_vars}."
        )

    sc.pp.scrublet(
        adata,
        log_transform=False,
        n_prin_comps=n_prin_comps,
        random_state=random_state,
        verbose=False,
    )

    for column in obs_before.columns:
        if column not in adata.obs.columns:
            adata.obs[column] = obs_before[column].reindex(adata.obs_names)

    predicted = adata.obs["predicted_doublet"].fillna(False).astype(bool)
    n_cells_scored = int(adata.n_obs)
    n_predicted_doublets = int(predicted.sum())
    filtered = adata[~predicted.to_numpy(), :].copy()
    if filtered.n_obs == 0:
        raise ValueError("Doublet detection removed all cells. Re-run with --doublet-method none.")

    summary = {
        "method": method,
        "n_cells_scored": n_cells_scored,
        "n_predicted_doublets": n_predicted_doublets,
        "n_cells_retained": int(filtered.n_obs),
        "predicted_doublet_rate": round(n_predicted_doublets / max(1, n_cells_scored), 4),
    }
    return filtered, summary


def run_preprocess(adata, n_top_hvg: int):
    """Normalise, log-transform, and prepare full-gene + HVG branches."""
    sc = _import_scanpy()

    adata_norm = adata.copy()
    sc.pp.normalize_total(adata_norm, target_sum=1e4)
    sc.pp.log1p(adata_norm)
    sc.pp.highly_variable_genes(adata_norm, n_top_genes=n_top_hvg, flavor="seurat")

    n_hvg = int(adata_norm.var["highly_variable"].sum())
    if n_hvg == 0:
        raise ValueError("No highly variable genes found.")

    adata_hvg = adata_norm[:, adata_norm.var["highly_variable"]].copy()
    return adata_norm, adata_hvg, n_hvg


def run_embedding_cluster(
    adata,
    n_pcs: int,
    n_neighbors: int,
    leiden_resolution: float,
    random_state: int,
    *,
    use_rep: str = "",
):
    """Compute graph neighbors, UMAP, and Leiden clusters from PCA or a latent rep."""
    sc = _import_scanpy()

    adata = adata.copy()
    if use_rep:
        if use_rep not in adata.obsm:
            raise ValueError(f"Requested latent representation not found in adata.obsm: {use_rep}")
        sc.pp.neighbors(adata, n_neighbors=n_neighbors, use_rep=use_rep)
        n_pcs_eff = 0
    else:
        sc.pp.scale(adata, max_value=10)

        n_pcs_eff = min(n_pcs, adata.n_obs - 1, adata.n_vars - 1)
        if n_pcs_eff < 1:
            raise ValueError(
                "PCA requires at least 2 cells and 2 genes after QC/HVG selection. "
                f"Got n_obs={adata.n_obs}, n_vars={adata.n_vars}."
            )
        sc.tl.pca(adata, n_comps=n_pcs_eff, random_state=random_state, svd_solver="arpack")
        sc.pp.neighbors(adata, n_neighbors=n_neighbors, n_pcs=n_pcs_eff)
    sc.tl.umap(adata, random_state=random_state)
    sc.tl.leiden(adata, resolution=leiden_resolution, random_state=random_state)

    return adata, n_pcs_eff


def run_markers(adata, top_markers: int = 10):
    """Run rank_genes_groups and return full + top marker tables."""
    sc = _import_scanpy()

    adata = adata.copy()
    sc.tl.rank_genes_groups(
        adata,
        groupby="leiden",
        method="wilcoxon",
        pts=True,
    )

    clusters = list(adata.obs["leiden"].cat.categories)
    dfs = []
    for cluster in clusters:
        df = sc.get.rank_genes_groups_df(adata, group=cluster)
        df.insert(0, "cluster", cluster)
        dfs.append(df)

    markers_all = pd.concat(dfs, axis=0, ignore_index=True)
    markers_top = (
        markers_all.sort_values(["cluster", "scores"], ascending=[True, False])
        .groupby("cluster", as_index=False, group_keys=False)
        .head(top_markers)
        .reset_index(drop=True)
    )
    return adata, markers_all, markers_top


def run_contrastive_markers(
    adata,
    *,
    groupby: str,
    group1: str,
    group2: str,
    top_genes: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run two-group contrastive markers with Wilcoxon and return full/top tables plus summary."""
    sc = _import_scanpy()

    if group1 == group2:
        raise ValueError("--contrast-group1 and --contrast-group2 must be different values.")

    if groupby not in adata.obs.columns:
        available_cols = ", ".join(sorted(map(str, adata.obs.columns.tolist())))
        raise ValueError(
            f"Contrastive marker groupby column not found in adata.obs: {groupby}. "
            f"Available columns: {available_cols}."
        )

    groups = adata.obs[groupby].astype(str)
    available_groups = sorted(groups.dropna().unique().tolist())
    missing_groups = [g for g in (group1, group2) if g not in available_groups]
    if missing_groups:
        raise ValueError(
            f"Contrastive marker group value(s) not found in {groupby}: {', '.join(missing_groups)}. "
            f"Available groups: {', '.join(available_groups)}."
        )

    mask = groups.isin([group1, group2]).to_numpy()
    if int(mask.sum()) < 2:
        raise ValueError(
            f"Contrastive marker analysis requires at least 2 cells across {group1} and {group2} in {groupby}."
        )

    adata_de = adata[mask].copy()
    de_groups = groups[mask].tolist()
    adata_de.obs["_de_group"] = pd.Categorical(
        de_groups,
        categories=[group1, group2],
        ordered=True,
    )

    n_group1 = int(sum(1 for g in de_groups if g == group1))
    n_group2 = int(sum(1 for g in de_groups if g == group2))
    if n_group1 == 0 or n_group2 == 0:
        raise ValueError(
            "Contrastive marker analysis requires both groups to have cells. "
            f"Got {group1}={n_group1}, {group2}={n_group2}."
        )

    sc.tl.rank_genes_groups(
        adata_de,
        groupby="_de_group",
        groups=[group1],
        reference=group2,
        method="wilcoxon",
        pts=True,
    )

    de_full = sc.get.rank_genes_groups_df(adata_de, group=group1).reset_index(drop=True)
    if de_full.empty:
        raise ValueError(
            f"Contrastive marker analysis did not return any genes for {group1} vs {group2} in {groupby}."
        )

    de_top = (
        de_full.sort_values("scores", ascending=False)
        .head(top_genes)
        .reset_index(drop=True)
    )

    summary = {
        "enabled": True,
        "groupby": groupby,
        "group1": group1,
        "group2": group2,
        "n_cells_group1": n_group1,
        "n_cells_group2": n_group2,
        "n_genes_full": int(len(de_full)),
        "top_table": "contrastive_markers_top.csv",
        "full_table": "contrastive_markers_full.csv",
        "top_gene_names": de_top["names"].dropna().astype(str).tolist(),
        "volcano_plot": "",
    }
    return de_full, de_top, summary


def attach_leiden_labels(graph_adata, expression_adata):
    """Attach graph-derived Leiden labels onto another AnnData with matching cells."""
    attached = expression_adata.copy()
    leiden_labels = graph_adata.obs["leiden"].astype(str).reindex(attached.obs_names)
    if bool(leiden_labels.isna().any()):
        raise ValueError("Internal error: missing Leiden labels for normalized cells.")
    category_order = sorted(pd.Index(leiden_labels.dropna().unique()), key=_cluster_sort_key)
    attached.obs["leiden"] = pd.Categorical(
        leiden_labels.tolist(),
        categories=category_order,
        ordered=False,
    )
    return attached


def aggregate_cluster_annotations(
    clusters: pd.Series,
    predicted_labels: pd.Series,
    conf_scores: pd.Series,
    model_name: str,
) -> pd.DataFrame:
    """Aggregate per-cell CellTypist predictions to cluster-level summaries."""
    frame = pd.DataFrame(
        {
            "cluster": clusters.astype(str),
            "predicted_cell_type": predicted_labels.astype(str),
            "conf_score": conf_scores.astype(float),
        },
        index=clusters.index,
    )

    rows = []
    cluster_order = sorted(frame["cluster"].unique().tolist(), key=_cluster_sort_key)
    for cluster in cluster_order:
        group = frame.loc[frame["cluster"] == cluster]
        counts = group["predicted_cell_type"].value_counts()
        max_count = int(counts.max())
        winning_labels = sorted(counts[counts == max_count].index.astype(str).tolist())
        majority_label = winning_labels[0]
        majority_mask = group["predicted_cell_type"] == majority_label
        rows.append(
            {
                "cluster": cluster,
                "n_cells": int(group.shape[0]),
                "predicted_cell_type": majority_label,
                "support_fraction": round(float(majority_mask.mean()), 4),
                "mean_confidence": round(
                    float(group.loc[majority_mask, "conf_score"].mean()),
                    4,
                ),
                "annotation_model": model_name,
            }
        )

    return pd.DataFrame(rows)


def run_celltypist_annotation(adata, model_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run optional local CellTypist annotation and return cluster-level summaries."""
    celltypist = _import_celltypist()
    model_path = resolve_celltypist_model_path(celltypist, model_name)
    model = celltypist.models.Model.load(str(model_path))

    overlap = int(np.isin(np.asarray(adata.var_names, dtype=object), model.features).sum())
    if overlap == 0:
        raise RuntimeError(
            "CellTypist annotation requires human gene symbols overlapping the local model. "
            f"Found 0 overlapping genes with {model_path.name}."
        )

    adata = adata.copy()
    adata.var_names_make_unique()
    result = celltypist.annotate(
        adata,
        model=model,
        majority_voting=False,
    )

    predicted = result.predicted_labels["predicted_labels"].reindex(adata.obs_names)
    conf_scores = result.probability_matrix.max(axis=1).reindex(adata.obs_names)
    annotations = aggregate_cluster_annotations(
        clusters=adata.obs["leiden"],
        predicted_labels=predicted,
        conf_scores=conf_scores,
        model_name=model_path.name,
    )
    metadata = {
        "backend": "celltypist",
        "model": model_path.name,
        "model_path": str(model_path),
        "overlap_genes": overlap,
        "putative": True,
        "n_clusters_annotated": int(annotations.shape[0]),
    }
    return annotations, metadata


def plot_core_figures(
    graph_adata,
    marker_adata,
    markers_top: pd.DataFrame,
    figures_dir: Path,
) -> list[Path]:
    """Create QC/UMAP/marker plots."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sc = _import_scanpy()
    figures_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    sc.pl.violin(
        graph_adata,
        ["n_genes_by_counts", "total_counts", "pct_counts_mt"],
        jitter=0.4,
        multi_panel=True,
        show=False,
    )
    plt.tight_layout()
    qc_path = figures_dir / "qc_violin.png"
    plt.savefig(qc_path, dpi=180, bbox_inches="tight")
    plt.close("all")
    created.append(qc_path)

    sc.pl.umap(graph_adata, color="leiden", legend_loc="on data", show=False)
    plt.tight_layout()
    umap_path = figures_dir / "umap_leiden.png"
    plt.savefig(umap_path, dpi=180, bbox_inches="tight")
    plt.close("all")
    created.append(umap_path)

    marker_genes = (
        markers_top.groupby("cluster", as_index=False)
        .head(3)["names"]
        .dropna()
        .astype(str)
        .tolist()
    )
    marker_genes = list(dict.fromkeys(marker_genes))[:20]
    if marker_genes:
        dot = sc.pl.dotplot(
            marker_adata,
            var_names=marker_genes,
            groupby="leiden",
            show=False,
            return_fig=True,
        )
        marker_path = figures_dir / "marker_dotplot.png"
        dot.savefig(marker_path, dpi=180)
        plt.close("all")
        created.append(marker_path)

    return created


def write_tables(
    adata,
    markers_top: pd.DataFrame,
    tables_dir: Path,
    doublet_summary: dict[str, Any] | None = None,
    annotation_table: pd.DataFrame | None = None,
) -> dict[str, Path]:
    """Write cluster, marker, and optional feature tables."""
    tables_dir.mkdir(parents=True, exist_ok=True)

    table_paths: dict[str, Path] = {}
    cluster_counts = adata.obs["leiden"].value_counts().sort_index()
    cluster_summary = pd.DataFrame(
        {
            "cluster": cluster_counts.index.astype(str),
            "n_cells": cluster_counts.values.astype(int),
            "proportion": (cluster_counts.values / max(1, int(adata.n_obs))).round(4),
        }
    )
    cluster_path = tables_dir / "cluster_summary.csv"
    cluster_summary.to_csv(cluster_path, index=False)
    table_paths["cluster_summary"] = cluster_path

    csv_path = tables_dir / "markers_top.csv"
    tsv_path = tables_dir / "markers_top.tsv"
    markers_top.to_csv(csv_path, index=False)
    markers_top.to_csv(tsv_path, sep="\t", index=False)
    table_paths["markers_top_csv"] = csv_path
    table_paths["markers_top_tsv"] = tsv_path

    if doublet_summary is not None:
        doublet_path = tables_dir / "doublet_summary.csv"
        pd.DataFrame([doublet_summary]).to_csv(doublet_path, index=False)
        table_paths["doublet_summary"] = doublet_path

    if annotation_table is not None:
        annotation_path = tables_dir / "cluster_annotations.csv"
        annotation_table.to_csv(annotation_path, index=False)
        table_paths["cluster_annotations"] = annotation_path

    return table_paths


def write_contrast_tables(
    contrast_full: pd.DataFrame,
    contrast_top: pd.DataFrame,
    tables_dir: Path,
) -> tuple[Path, Path]:
    """Write contrastive marker full/top tables."""
    tables_dir.mkdir(parents=True, exist_ok=True)
    contrast_full_path = tables_dir / "contrastive_markers_full.csv"
    contrast_top_path = tables_dir / "contrastive_markers_top.csv"
    contrast_full.to_csv(contrast_full_path, index=False)
    contrast_top.to_csv(contrast_top_path, index=False)
    return contrast_full_path, contrast_top_path


def plot_contrast_volcano(
    contrast_full: pd.DataFrame,
    figures_dir: Path,
    *,
    group1: str,
    group2: str,
) -> Path:
    """Create contrastive marker volcano plot from full two-group results."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures_dir.mkdir(parents=True, exist_ok=True)

    if "logfoldchanges" not in contrast_full.columns:
        raise ValueError("Contrastive markers table missing required column: logfoldchanges")

    p_col = "pvals_adj" if "pvals_adj" in contrast_full.columns else "pvals"
    if p_col not in contrast_full.columns:
        raise ValueError("Contrastive markers table missing required p-value column (pvals_adj or pvals).")

    log_fc = pd.to_numeric(contrast_full["logfoldchanges"], errors="coerce").to_numpy(dtype=np.float64)
    pvals = pd.to_numeric(contrast_full[p_col], errors="coerce").to_numpy(dtype=np.float64)
    pvals = np.clip(pvals, 1e-300, 1.0)
    neg_log10 = -np.log10(pvals)
    finite_mask = np.isfinite(log_fc) & np.isfinite(neg_log10)
    if int(finite_mask.sum()) == 0:
        raise ValueError("No finite contrastive marker points available for volcano plot.")

    sig_mask = finite_mask & (pvals < 0.05) & (np.abs(log_fc) >= 1.0)
    up_mask = sig_mask & (log_fc > 0)
    down_mask = sig_mask & (log_fc < 0)
    nonsig_mask = finite_mask & (~sig_mask)

    fig, ax = plt.subplots(figsize=(7.2, 5.6))
    ax.scatter(
        log_fc[nonsig_mask],
        neg_log10[nonsig_mask],
        s=10,
        c="#94a3b8",
        alpha=0.65,
        edgecolors="none",
        label="Not significant",
    )
    if int(up_mask.sum()) > 0:
        ax.scatter(
            log_fc[up_mask],
            neg_log10[up_mask],
            s=16,
            c="#dc2626",
            alpha=0.85,
            edgecolors="none",
            label=f"Up in {group1}",
        )
    if int(down_mask.sum()) > 0:
        ax.scatter(
            log_fc[down_mask],
            neg_log10[down_mask],
            s=16,
            c="#2563eb",
            alpha=0.85,
            edgecolors="none",
            label=f"Up in {group2}",
        )

    ax.axvline(-1.0, color="#64748b", linewidth=0.9, linestyle="--")
    ax.axvline(1.0, color="#64748b", linewidth=0.9, linestyle="--")
    ax.axhline(-np.log10(0.05), color="#64748b", linewidth=0.9, linestyle="--")
    ax.set_xlabel("log2 fold change")
    y_label = "-log10(adjusted p-value)" if p_col == "pvals_adj" else "-log10(p-value)"
    ax.set_ylabel(y_label)
    ax.set_title(f"Contrastive Markers Volcano: {group1} vs {group2}")
    ax.legend(loc="upper right", frameon=False)
    ax.grid(alpha=0.18, linewidth=0.5)

    plot_path = figures_dir / "contrastive_markers_volcano.png"
    fig.tight_layout()
    fig.savefig(plot_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return plot_path


def render_report(
    output_dir: Path,
    input_path: Path | None,
    input_source: dict[str, Any] | None,
    is_demo: bool,
    demo_source: str | None,
    qc_stats: dict[str, int],
    n_hvg: int,
    n_clusters: int,
    n_pcs_eff: int,
    params: dict[str, Any],
    table_paths: dict[str, Path],
    figure_paths: list[Path],
    n_cells_analyzed: int,
    contrast_summary: dict[str, Any],
    doublet_summary: dict[str, Any] | None = None,
    annotation_table: pd.DataFrame | None = None,
    annotation_info: dict[str, Any] | None = None,
) -> Path:
    """Create markdown report.md."""
    input_files = [Path(path) for path in input_source["files"]] if input_source else []
    header = generate_report_header(
        title="scRNA Orchestrator Report",
        skill_name="scrna-orchestrator",
        input_files=input_files,
        extra_metadata={
            "Mode": "demo" if is_demo else "input",
            "Input format": "demo" if is_demo else input_source["format"] if input_source else "unknown",
            "Cells (before QC)": str(qc_stats["n_cells_before"]),
            "Cells (after QC)": str(n_cells_analyzed),
            "Genes (after QC)": str(qc_stats["n_genes_after"]),
            "Leiden clusters": str(n_clusters),
            "HVG selected": str(n_hvg),
            "Graph basis": params["graph_basis"],
            "Doublet method": params["doublet_method"],
            "Annotation backend": params["annotate"],
            "Demo source": demo_source if is_demo and demo_source else "n/a",
        },
    )

    figure_labels = {
        "qc_violin.png": "QC Violin",
        "umap_leiden.png": "UMAP Leiden",
        "marker_dotplot.png": "Marker Dotplot",
    }
    lines = ["## Summary", ""]
    lines.append(f"- Cells before QC: **{qc_stats['n_cells_before']}**")
    lines.append(f"- Cells after QC: **{n_cells_analyzed}**")
    if doublet_summary is not None:
        lines.append(f"- Cells after filter-only QC: **{qc_stats['n_cells_after']}**")
        lines.append(f"- Predicted doublets: **{doublet_summary['n_predicted_doublets']}**")
        lines.append(
            "- Doublet detection: "
            f"**{doublet_summary['method']}** ({doublet_summary['predicted_doublet_rate']:.1%} predicted)"
        )
    lines.append(f"- Genes before QC: **{qc_stats['n_genes_before']}**")
    lines.append(f"- Genes after QC: **{qc_stats['n_genes_after']}**")
    lines.append(f"- HVGs selected: **{n_hvg}**")
    lines.append(f"- Leiden clusters: **{n_clusters}**")
    if annotation_info is not None:
        lines.append(f"- Clusters annotated: **{annotation_info['n_clusters_annotated']}**")

    lines.extend(["", "## Core Figures", ""])
    for figure_path in figure_paths:
        if figure_path.name not in figure_labels:
            continue
        lines.append(f"![{figure_labels[figure_path.name]}](figures/{figure_path.name})")

    lines.extend(["", "## Tables", ""])
    for table_path in table_paths.values():
        lines.append(f"- `tables/{table_path.name}`")

    if doublet_summary is not None:
        lines.extend(["", "## Doublet Detection", ""])
        lines.append("- Method: `scanpy.pp.scrublet`")
        lines.append(f"- Cells scored: **{doublet_summary['n_cells_scored']}**")
        lines.append(f"- Predicted doublets: **{doublet_summary['n_predicted_doublets']}**")
        lines.append(f"- Cells retained: **{doublet_summary['n_cells_retained']}**")
        lines.append("- Summary table: `tables/doublet_summary.csv`")

    if annotation_table is not None and annotation_info is not None:
        lines.extend(["", "## Cell Type Annotation", ""])
        lines.append("- Backend: `CellTypist`")
        lines.append(f"- Model: `{annotation_info['model']}`")
        lines.append(f"- Overlapping genes with model: **{annotation_info['overlap_genes']}**")
        lines.append("- Labels are **putative**, model-based assignments and should be manually reviewed.")
        lines.append("- Summary table: `tables/cluster_annotations.csv`")
        lines.append("")
        for row in annotation_table.itertuples(index=False):
            lines.append(
                f"- Cluster `{row.cluster}` -> **{row.predicted_cell_type}** "
                f"(support={row.support_fraction:.2f}, mean_confidence={row.mean_confidence:.2f})"
            )

    lines.extend(["", "## Contrastive Marker Analysis (Two-Group)", ""])
    top_genes = contrast_summary.get("top_gene_names", [])
    if contrast_summary.get("enabled"):
        lines.append(f"- Grouping column: `{contrast_summary['groupby']}`")
        lines.append(f"- Comparison: `{contrast_summary['group1']}` vs `{contrast_summary['group2']}`")
        lines.append(
            f"- Cells in groups: `{contrast_summary['group1']}={contrast_summary['n_cells_group1']}`, "
            f"`{contrast_summary['group2']}={contrast_summary['n_cells_group2']}`"
        )
        lines.append(f"- Genes in full table: **{contrast_summary['n_genes_full']}**")
        lines.append(f"- Full table: `tables/{contrast_summary['full_table']}`")
        lines.append(f"- Top table: `tables/{contrast_summary['top_table']}`")
        volcano_plot_name = str(contrast_summary.get("volcano_plot", "")).strip()
        if volcano_plot_name:
            lines.append(f"- Volcano plot: `figures/{volcano_plot_name}`")
        else:
            lines.append("- Volcano plot: not generated (use `--contrast-volcano`)")
        lines.append("")
        lines.append("Top contrastive marker genes by score:")
        if top_genes:
            lines.extend([f"- `{gene}`" for gene in top_genes[:10]])
        else:
            lines.append("- None")
        if volcano_plot_name:
            lines.extend(["", f"![Contrastive Marker Volcano](figures/{volcano_plot_name})"])
        contrast_methods = (
            "- Contrastive marker analysis: `scanpy.tl.rank_genes_groups` "
            f"(Wilcoxon, `{contrast_summary['group1']}` vs `{contrast_summary['group2']}`, "
            f"`groupby={contrast_summary['groupby']}`)"
        )
        if volcano_plot_name:
            contrast_methods += "; volcano plot with thresholds `p<0.05`, `|log2FC|>=1`"
    else:
        lines.append(
            "- Not enabled for this run (use `--contrast-groupby --contrast-group1 --contrast-group2`)."
        )
        contrast_methods = "- Contrastive marker analysis: not enabled"

    lines.extend(["", "## Methods", ""])
    lines.append(
        "- QC/filtering: "
        f"`min_genes={params['min_genes']}`, `min_cells={params['min_cells']}`, `max_mt_pct={params['max_mt_pct']}`"
    )
    if doublet_summary is not None:
        lines.append(
            "- Doublet detection: "
            "`scanpy.pp.scrublet` on QC-filtered raw counts before normalization/clustering"
        )
    lines.append("- Normalisation: total-count normalisation (`target_sum=1e4`) + `log1p`")
    lines.append(f"- Feature selection: `n_top_hvg={params['n_top_hvg']}`")
    if params["graph_basis"] == "pca":
        lines.append(
            "- Graph construction: "
            f"PCA (`n_pcs={n_pcs_eff}`) + neighbors (`n_neighbors={params['n_neighbors']}`) + UMAP"
        )
    else:
        lines.append(
            "- Graph construction: "
            f"latent representation `{params['graph_basis']}` + neighbors "
            f"(`n_neighbors={params['n_neighbors']}`) + UMAP"
        )
    lines.append(f"- Clustering: Leiden `resolution={params['leiden_resolution']}`")
    lines.append(
        "- Marker analysis: `scanpy.tl.rank_genes_groups` "
        "(Wilcoxon, cluster-vs-rest) on normalized full-gene expression"
    )
    if annotation_info is not None:
        lines.append(
            "- Annotation: "
            f"`CellTypist` model `{annotation_info['model']}` on normalized/log1p full-gene expression"
        )
    lines.append(contrast_methods)

    lines.extend(["", "## Reproducibility", "", "See:"])
    lines.append("- `reproducibility/commands.sh`")
    lines.append("- `reproducibility/environment.yml`")
    lines.append("- `reproducibility/checksums.sha256`")

    report_path = output_dir / "report.md"
    report_path.write_text(header + "\n".join(lines) + generate_report_footer(), encoding="utf-8")
    return report_path


def build_repro_command(
    output_dir: Path,
    input_path: Path | None,
    is_demo: bool,
    args: argparse.Namespace,
    *,
    resolved_use_rep: str,
    contrast_request: dict[str, str] | None,
    contrast_options: dict[str, Any],
) -> str:
    """Build a reproducible CLI command for commands.sh."""
    parts = ["python", "skills/scrna-orchestrator/scrna_orchestrator.py"]
    if is_demo:
        parts.append("--demo")
    else:
        if input_path is None:
            raise ValueError("input_path is required when --demo is not used.")
        parts.extend(["--input", str(input_path)])

    parts.extend(["--output", str(output_dir)])
    if resolved_use_rep:
        parts.extend(["--use-rep", resolved_use_rep])
    elif (args.use_rep or "auto").strip() == "none":
        parts.extend(["--use-rep", "none"])

    tunable_defaults = [
        ("--min-genes", args.min_genes, 200),
        ("--min-cells", args.min_cells, 3),
        ("--max-mt-pct", args.max_mt_pct, 20.0),
        ("--n-top-hvg", args.n_top_hvg, 2000),
        ("--n-pcs", args.n_pcs, 50),
        ("--n-neighbors", args.n_neighbors, 15),
        ("--leiden-resolution", args.leiden_resolution, 1.0),
        ("--random-state", args.random_state, 0),
        ("--top-markers", args.top_markers, 10),
    ]
    for flag, value, default in tunable_defaults:
        if value != default:
            parts.extend([flag, str(value)])

    if args.doublet_method != "none":
        parts.extend(["--doublet-method", args.doublet_method])
    if args.annotate != "none":
        parts.extend(["--annotate", args.annotate])
        parts.extend(["--annotation-model", args.annotation_model])
    if contrast_request:
        parts.extend(["--contrast-groupby", contrast_request["groupby"]])
        parts.extend(["--contrast-group1", contrast_request["group1"]])
        parts.extend(["--contrast-group2", contrast_request["group2"]])
        parts.extend(["--contrast-top-genes", str(contrast_options["top_genes"])])
        if contrast_options["volcano"]:
            parts.append("--contrast-volcano")

    return " ".join(shlex.quote(part) for part in parts)


def write_reproducibility(
    output_dir: Path,
    input_path: Path | None,
    input_source: dict[str, Any] | None,
    is_demo: bool,
    args: argparse.Namespace,
    table_paths: dict[str, Path],
    figure_paths: list[Path],
    *,
    resolved_use_rep: str,
    contrast_request: dict[str, str] | None,
    contrast_options: dict[str, Any],
) -> None:
    """Write commands.sh, environment.yml, and checksums.sha256."""
    repro_dir = output_dir / "reproducibility"
    repro_dir.mkdir(parents=True, exist_ok=True)
    cmd_line = build_repro_command(
        output_dir,
        input_path,
        is_demo,
        args,
        resolved_use_rep=resolved_use_rep,
        contrast_request=contrast_request,
        contrast_options=contrast_options,
    )

    commands = f"""#!/usr/bin/env bash
# Reproducibility script — ClawBio scRNA Orchestrator
# Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

set -euo pipefail

{cmd_line}
"""
    (repro_dir / "commands.sh").write_text(commands, encoding="utf-8")

    env_yml = """name: clawbio-scrna
channels:
  - conda-forge
  - bioconda
  - defaults
dependencies:
  - python=3.11
  - scanpy=1.10.2
  - anndata=0.10.8
  - numpy=1.26.4
  - pandas=2.2.2
  - matplotlib=3.8.4
  - seaborn=0.13.2
  - leidenalg=0.10.2
  - python-igraph=0.11.6
  - pip
  - pip:
      - scrublet==0.2.3
      - celltypist==1.7.1
"""
    (repro_dir / "environment.yml").write_text(env_yml, encoding="utf-8")

    checksum_targets: list[Path] = []
    if input_source is not None:
        checksum_targets.extend(Path(path) for path in input_source["files"] if Path(path).exists())
    checksum_targets.extend(
        [
            output_dir / "report.md",
            output_dir / "result.json",
            *table_paths.values(),
            *figure_paths,
        ]
    )

    lines: list[str] = []
    for path in checksum_targets:
        if not path.exists():
            continue
        rel = path.relative_to(output_dir) if path.is_relative_to(output_dir) else path.name
        lines.append(f"{sha256_file(path)}  {rel}")
    (repro_dir / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    """Run the full scRNA pipeline."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    tables_dir = output_dir / "tables"
    figures_dir.mkdir(exist_ok=True)
    tables_dir.mkdir(exist_ok=True)

    contrast_request, contrast_options = resolve_contrast_request(args)
    contrast_summary: dict[str, Any] = {
        "enabled": False,
        "groupby": "",
        "group1": "",
        "group2": "",
        "n_cells_group1": 0,
        "n_cells_group2": 0,
        "n_genes_full": 0,
        "top_table": "",
        "full_table": "",
        "top_gene_names": [],
        "volcano_plot": "",
    }

    adata, input_path, is_demo, demo_source, input_source, latent_context = load_data(
        args.input,
        args.demo,
        args.random_state,
        use_rep=args.use_rep,
    )
    adata_qc, qc_stats = qc_filter(
        adata,
        min_genes=args.min_genes,
        min_cells=args.min_cells,
        max_mt_pct=args.max_mt_pct,
    )
    adata_clean, doublet_summary = run_doublet_detection(
        adata_qc,
        method=args.doublet_method,
        random_state=args.random_state,
    )
    adata_norm, adata_hvg, n_hvg = run_preprocess(adata_clean, n_top_hvg=args.n_top_hvg)
    graph_input = adata_norm if latent_context["resolved_use_rep"] else adata_hvg
    adata_graph, n_pcs_eff = run_embedding_cluster(
        graph_input,
        n_pcs=args.n_pcs,
        n_neighbors=args.n_neighbors,
        leiden_resolution=args.leiden_resolution,
        random_state=args.random_state,
        use_rep=latent_context["resolved_use_rep"],
    )
    adata_expression = attach_leiden_labels(adata_graph, adata_norm)
    adata_markers, _, markers_top = run_markers(
        adata_expression,
        top_markers=args.top_markers,
    )

    annotation_table = None
    annotation_info = None
    if args.annotate != "none":
        annotation_table, annotation_info = run_celltypist_annotation(
            adata_markers.copy(),
            model_name=args.annotation_model,
        )

    contrast_full = None
    contrast_top = None
    if contrast_request:
        contrast_full, contrast_top, contrast_summary = run_contrastive_markers(
            adata_markers.copy(),
            groupby=contrast_request["groupby"],
            group1=contrast_request["group1"],
            group2=contrast_request["group2"],
            top_genes=contrast_options["top_genes"],
        )

    table_paths = write_tables(
        adata_graph,
        markers_top,
        tables_dir,
        doublet_summary=doublet_summary,
        annotation_table=annotation_table,
    )
    if contrast_request and contrast_full is not None and contrast_top is not None:
        contrast_full_path, contrast_top_path = write_contrast_tables(
            contrast_full,
            contrast_top,
            tables_dir,
        )
        table_paths["contrastive_markers_full"] = contrast_full_path
        table_paths["contrastive_markers_top"] = contrast_top_path

    figure_paths = plot_core_figures(adata_graph, adata_markers, markers_top, figures_dir)
    if contrast_request and contrast_full is not None and contrast_options["volcano"]:
        volcano_path = plot_contrast_volcano(
            contrast_full,
            figures_dir,
            group1=contrast_summary["group1"],
            group2=contrast_summary["group2"],
        )
        contrast_summary["volcano_plot"] = volcano_path.name
        figure_paths.append(volcano_path)

    n_clusters = int(adata_graph.obs["leiden"].nunique())
    n_cells_analyzed = int(adata_graph.n_obs)
    params = {
        "min_genes": args.min_genes,
        "min_cells": args.min_cells,
        "max_mt_pct": args.max_mt_pct,
        "n_top_hvg": args.n_top_hvg,
        "n_pcs": args.n_pcs,
        "n_neighbors": args.n_neighbors,
        "leiden_resolution": args.leiden_resolution,
        "random_state": args.random_state,
        "doublet_method": args.doublet_method,
        "annotate": args.annotate,
        "annotation_model": args.annotation_model,
        "graph_basis": latent_context["graph_basis"],
    }
    report_path = render_report(
        output_dir=output_dir,
        input_path=input_path,
        input_source=input_source,
        is_demo=is_demo,
        demo_source=demo_source,
        qc_stats=qc_stats,
        n_hvg=n_hvg,
        n_clusters=n_clusters,
        n_pcs_eff=n_pcs_eff,
        params=params,
        table_paths=table_paths,
        figure_paths=figure_paths,
        n_cells_analyzed=n_cells_analyzed,
        contrast_summary=contrast_summary,
        doublet_summary=doublet_summary,
        annotation_table=annotation_table,
        annotation_info=annotation_info,
    )

    summary = {
        "n_cells_before": qc_stats["n_cells_before"],
        "n_cells_after": n_cells_analyzed,
        "n_genes_before": qc_stats["n_genes_before"],
        "n_genes_after": qc_stats["n_genes_after"],
        "n_hvg": n_hvg,
        "n_clusters": n_clusters,
        "graph_basis": latent_context["graph_basis"],
        "use_rep_resolved": latent_context["resolved_use_rep"],
    }
    if doublet_summary is not None:
        summary["n_predicted_doublets"] = doublet_summary["n_predicted_doublets"]
    if annotation_info is not None:
        summary["n_clusters_annotated"] = annotation_info["n_clusters_annotated"]
    if latent_context["counts_layer"]:
        summary["counts_layer"] = latent_context["counts_layer"]

    data: dict[str, Any] = {
        "cluster_labels": sorted(
            adata_graph.obs["leiden"].astype(str).unique().tolist(),
            key=_cluster_sort_key,
        ),
        "input": {
            "format": "demo" if is_demo else input_source["format"] if input_source else "unknown",
            "files": [] if input_source is None else [Path(path).name for path in input_source["files"]],
        },
        "tables": [path.name for path in table_paths.values()],
        "figures": [path.name for path in figure_paths],
        "demo_source": demo_source if is_demo else "not_demo",
        "graph_basis": latent_context["graph_basis"],
        "use_rep_resolved": latent_context["resolved_use_rep"],
        "counts_layer": latent_context["counts_layer"],
        "disclaimer": DISCLAIMER,
    }
    contrast_payload = {
        "enabled": bool(contrast_summary["enabled"]),
        "groupby": contrast_summary["groupby"] if contrast_summary["enabled"] else "",
        "group1": contrast_summary["group1"] if contrast_summary["enabled"] else "",
        "group2": contrast_summary["group2"] if contrast_summary["enabled"] else "",
        "n_genes_full": int(contrast_summary["n_genes_full"]) if contrast_summary["enabled"] else 0,
        "full_table": contrast_summary["full_table"] if contrast_summary["enabled"] else "",
        "top_table": contrast_summary["top_table"] if contrast_summary["enabled"] else "",
        "volcano_plot": contrast_summary["volcano_plot"] if contrast_summary["enabled"] else "",
        "used_legacy_flags": bool(contrast_options["used_legacy_flags"]),
    }
    data["contrastive_markers"] = contrast_payload
    data["de"] = contrast_payload.copy()
    if doublet_summary is not None:
        doublet_table = table_paths.get("doublet_summary")
        data["doublet"] = {
            **doublet_summary,
            "table": doublet_table.name if doublet_table else "",
        }
    if annotation_info is not None:
        annotation_path = table_paths.get("cluster_annotations")
        data["annotation"] = {
            **annotation_info,
            "table": annotation_path.name if annotation_path else "",
        }

    write_result_json(
        output_dir=output_dir,
        skill="scrna",
        version="0.1.0",
        summary=summary,
        data=data,
        input_checksum=compute_input_checksum(input_source),
    )

    write_reproducibility(
        output_dir,
        input_path,
        input_source,
        is_demo,
        args,
        table_paths=table_paths,
        figure_paths=figure_paths,
        resolved_use_rep=latent_context["resolved_use_rep"],
        contrast_request=contrast_request,
        contrast_options=contrast_options,
    )

    return {
        "report_path": report_path,
        "output_dir": output_dir,
        "n_clusters": n_clusters,
        "n_cells_after": n_cells_analyzed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ClawBio scRNA Orchestrator — Scanpy QC/clustering/markers with optional "
            "doublet detection, CellTypist annotation, and contrastive marker analysis"
        ),
    )
    parser.add_argument(
        "--input",
        "-i",
        help="Input raw-count .h5ad, matrix.mtx(.gz), or 10x Matrix Market directory",
    )
    parser.add_argument("--output", "-o", default="scrna_report", help="Output directory")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run demo data (PBMC3k raw preferred, fallback to synthetic)",
    )
    parser.add_argument("--min-genes", type=int, default=200, help="Minimum genes per cell")
    parser.add_argument("--min-cells", type=int, default=3, help="Minimum cells per gene")
    parser.add_argument("--max-mt-pct", type=float, default=20.0, help="Maximum mitochondrial percentage")
    parser.add_argument("--n-top-hvg", type=int, default=2000, help="Number of highly variable genes")
    parser.add_argument("--n-pcs", type=int, default=50, help="Number of principal components")
    parser.add_argument("--n-neighbors", type=int, default=15, help="Number of neighbors for graph construction")
    parser.add_argument(
        "--use-rep",
        default="auto",
        help="Graph representation: `auto`, `none`, or an `.obsm` key such as `X_scvi`",
    )
    parser.add_argument("--leiden-resolution", type=float, default=1.0, help="Leiden resolution")
    parser.add_argument("--random-state", type=int, default=0, help="Random seed")
    parser.add_argument("--top-markers", type=int, default=10, help="Top markers per cluster")
    parser.add_argument(
        "--doublet-method",
        choices=("none", "scrublet"),
        default="none",
        help="Optional doublet detection method",
    )
    parser.add_argument(
        "--annotate",
        choices=("none", "celltypist"),
        default="none",
        help="Optional cell type annotation backend",
    )
    parser.add_argument(
        "--annotation-model",
        default=DEFAULT_CELLTYPIST_MODEL,
        help="Local CellTypist model name or path (used with --annotate celltypist)",
    )
    parser.add_argument("--contrast-groupby", default=None, help="obs column for two-group contrastive markers")
    parser.add_argument("--contrast-group1", default=None, help="Group 1 value for contrastive markers")
    parser.add_argument("--contrast-group2", default=None, help="Group 2 reference value for contrastive markers")
    parser.add_argument(
        "--contrast-top-genes",
        type=int,
        default=None,
        help="Top contrastive marker genes to include in the summary table",
    )
    parser.add_argument(
        "--contrast-volcano",
        action="store_true",
        help="Generate optional contrastive markers volcano plot",
    )
    parser.add_argument("--de-groupby", default=None, help="Deprecated alias for --contrast-groupby")
    parser.add_argument("--de-group1", default=None, help="Deprecated alias for --contrast-group1")
    parser.add_argument("--de-group2", default=None, help="Deprecated alias for --contrast-group2")
    parser.add_argument(
        "--de-top-genes",
        type=int,
        default=None,
        help="Deprecated alias for --contrast-top-genes",
    )
    parser.add_argument("--de-volcano", action="store_true", help="Deprecated alias for --contrast-volcano")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.demo and not args.input:
        print("ERROR: Provide --input <input.h5ad|matrix.mtx|10x_dir> or --demo", file=sys.stderr)
        sys.exit(1)

    try:
        result = run_pipeline(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\nscRNA Orchestrator complete")
    print(f"  Report: {result['report_path']}")
    print(f"  Output: {result['output_dir']}")
    print(f"  Cells after QC: {result['n_cells_after']}")
    print(f"  Leiden clusters: {result['n_clusters']}")


if __name__ == "__main__":
    main()
