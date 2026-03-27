"""HyenaDNA model wrapper for variant scoring.

Handles lazy loading of the HyenaDNA-small-32k model from HuggingFace,
sequence scoring via log-likelihood at the variant position, and
disruption score interpretation.

Scoring method: For a variant at position P, the model predicts the
probability of each nucleotide at position P given the preceding context
(positions 0..P-1).  The disruption score is the log-odds ratio:
    log P(ref | left_context) - log P(alt | left_context)
This gives a clean signal at the variant position without dilution from
averaging over the entire sequence.

The model auto-downloads (~43 MB) on first use and is cached in
~/.cache/huggingface/.  CPU inference only — no GPU required.
"""

from __future__ import annotations

import sys
from typing import Any

# Model identifier on HuggingFace
MODEL_ID = "LongSafari/hyenadna-small-32k-seqlen-hf"

# Disruption score tier thresholds (log-odds scale)
# Calibrated: |log-odds| > 1 means the model assigns >2.7x higher probability
# to one allele over the other at that position
TIER_THRESHOLDS = {
    "high": 2.0,
    "moderate": 1.0,
    "low": 0.5,
}

# Nucleotide to token mapping (built at first use)
_nuc_token_ids: dict[str, int] | None = None


def check_dependencies() -> tuple[bool, str]:
    """Check if torch and transformers are installed.

    Returns:
        (available, message) — True if ready, or False with install instructions.
    """
    missing = []
    try:
        import torch  # noqa: F401
    except ImportError:
        missing.append("torch")
    try:
        import transformers  # noqa: F401
    except ImportError:
        missing.append("transformers")

    if missing:
        pkgs = " ".join(missing)
        return False, (
            f"Missing dependencies: {', '.join(missing)}.\n"
            f"Install with: pip install {pkgs}\n"
            f"(CPU-only PyTorch is sufficient — no GPU required)"
        )
    return True, "Dependencies available"


def load_model() -> tuple[Any, Any]:
    """Load HyenaDNA-small-32k model and tokenizer from HuggingFace.

    Returns:
        (model, tokenizer) — ready for inference.

    Raises:
        ImportError: if torch or transformers not installed.
    """
    ok, msg = check_dependencies()
    if not ok:
        raise ImportError(msg)

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"  Loading HyenaDNA model ({MODEL_ID})...", file=sys.stderr)

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_ID, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, trust_remote_code=True, torch_dtype=torch.float32
    )
    model.eval()

    # Build nucleotide token ID lookup
    global _nuc_token_ids
    _nuc_token_ids = {}
    for nuc in "ACGT":
        ids = tokenizer.encode(nuc, add_special_tokens=False)
        if ids:
            _nuc_token_ids[nuc] = ids[0]

    print("  Model loaded successfully.", file=sys.stderr)
    return model, tokenizer


def score_sequence(model: Any, tokenizer: Any, sequence: str) -> float:
    """Compute the mean log-likelihood of a DNA sequence under HyenaDNA.

    Args:
        model: HyenaDNA model.
        tokenizer: HyenaDNA tokenizer.
        sequence: DNA sequence string (ACGT characters).

    Returns:
        Mean log-likelihood (negative; closer to 0 = more likely).
    """
    import torch

    sequence = sequence.upper()
    inputs = tokenizer(sequence, return_tensors="pt")
    input_ids = inputs["input_ids"]

    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits  # (1, seq_len, vocab_size)

    # Shift: predict token t+1 from position t
    shift_logits = logits[:, :-1, :].contiguous()
    shift_labels = input_ids[:, 1:].contiguous()

    log_probs = torch.nn.functional.log_softmax(shift_logits, dim=-1)
    token_log_probs = log_probs.gather(2, shift_labels.unsqueeze(-1)).squeeze(-1)

    mean_ll = token_log_probs.mean().item()
    return mean_ll


def score_variant(
    model: Any,
    tokenizer: Any,
    context: str,
    center_pos: int,
    ref: str,
    alt: str,
) -> dict:
    """Score a single variant using position-specific log-likelihood.

    Runs the model once on the reference sequence, then extracts the
    log-probability of both ref and alt nucleotides at the variant
    position.  The disruption score is the absolute log-odds difference.

    Args:
        model: HyenaDNA model.
        tokenizer: HyenaDNA tokenizer.
        context: Flanking DNA sequence with ref allele at center_pos.
        center_pos: Position of the variant within the context string.
        ref: Reference allele (single base).
        alt: Alternate allele (single base).

    Returns:
        Dict with log_likelihood_ref, log_likelihood_alt, disruption_score, tier.
    """
    import torch

    ref = ref.upper()
    alt = alt.upper()
    context = context.upper()

    # Run model once on the reference sequence
    inputs = tokenizer(context, return_tensors="pt")
    input_ids = inputs["input_ids"]

    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits  # (1, seq_len, vocab_size)

    # Get log-probabilities at all positions
    log_probs = torch.nn.functional.log_softmax(logits, dim=-1)

    # The model predicts token at position t+1 from position t.
    # To get the prediction FOR position center_pos, we look at
    # the output AT position center_pos - 1 (which predicts center_pos).
    # But we need to account for any special tokens the tokenizer adds.
    #
    # For HyenaDNA, tokenizer typically adds a BOS token, so the
    # token at index i in input_ids corresponds to character i-1
    # in the original sequence (with index 0 being BOS).
    #
    # The prediction for character at `center_pos` in the sequence
    # comes from logits at the token index corresponding to that position.

    # Find the token index for the variant position
    # HyenaDNA character-level tokenizer: each DNA base is one token
    # Check if there's a BOS/special prefix
    seq_tokens = tokenizer.encode(context, add_special_tokens=False)
    full_tokens = input_ids[0].tolist()
    offset = len(full_tokens) - len(seq_tokens)
    variant_token_idx = offset + center_pos

    # Get the log-prob distribution at the position PREDICTING the variant
    # (causal LM: position t predicts token t+1)
    pred_idx = variant_token_idx - 1
    if pred_idx < 0:
        pred_idx = 0

    pred_log_probs = log_probs[0, pred_idx, :]  # (vocab_size,)

    # Get log-probs for ref and alt nucleotides
    ref_token_id = _nuc_token_ids.get(ref)
    alt_token_id = _nuc_token_ids.get(alt)

    if ref_token_id is None or alt_token_id is None:
        # Fallback: if we can't find token IDs, use mean-LL method
        return _score_variant_mean_ll(model, tokenizer, context, center_pos, ref, alt)

    ll_ref = pred_log_probs[ref_token_id].item()
    ll_alt = pred_log_probs[alt_token_id].item()

    disruption = abs(ll_ref - ll_alt)
    tier = interpret_score(disruption)

    return {
        "log_likelihood_ref": round(ll_ref, 4),
        "log_likelihood_alt": round(ll_alt, 4),
        "disruption_score": round(disruption, 4),
        "tier": tier,
    }


def _score_variant_mean_ll(
    model: Any,
    tokenizer: Any,
    context: str,
    center_pos: int,
    ref: str,
    alt: str,
) -> dict:
    """Fallback: score variant by comparing mean log-likelihood of full sequences."""
    ref_seq = context
    alt_seq = context[:center_pos] + alt + context[center_pos + 1:]

    ll_ref = score_sequence(model, tokenizer, ref_seq)
    ll_alt = score_sequence(model, tokenizer, alt_seq)

    disruption = abs(ll_ref - ll_alt)
    tier = interpret_score(disruption)

    return {
        "log_likelihood_ref": round(ll_ref, 4),
        "log_likelihood_alt": round(ll_alt, 4),
        "disruption_score": round(disruption, 4),
        "tier": tier,
    }


def interpret_score(score: float) -> str:
    """Map a disruption score to a severity tier.

    Args:
        score: Absolute disruption score (|ll_ref - ll_alt|).

    Returns:
        One of "high", "moderate", "low", "benign".
    """
    if score >= TIER_THRESHOLDS["high"]:
        return "high"
    elif score >= TIER_THRESHOLDS["moderate"]:
        return "moderate"
    elif score >= TIER_THRESHOLDS["low"]:
        return "low"
    else:
        return "benign"
