# -*- coding: utf-8 -*-
"""additive_db.py — Additive knowledge base lookup with fuzzy matching."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


_DB_PATH = Path(__file__).resolve().parent / "data" / "additives.json"


def _normalise(name: str) -> str:
    """Lower-case, strip parenthetical E-numbers, collapse whitespace."""
    name = name.lower().strip()
    # Strip trailing "(E123)" or "(e123)" patterns
    name = re.sub(r"\s*\(e\d{3,4}[a-z]?\)\s*$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


class AdditiveDB:
    """In-memory additive knowledge base with fuzzy lookup."""

    def __init__(self, db_path: str | Path | None = None):
        db_path = Path(db_path) if db_path else _DB_PATH
        with open(db_path, encoding="utf-8") as f:
            raw = json.load(f)
        self.entries: list[dict[str, Any]] = raw["additives"]
        # Build name → entry index
        self._index: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            for alias in entry["names"]:
                self._index[_normalise(alias)] = entry
            # Also index by E-number itself
            self._index[entry["id"].lower()] = entry

    def lookup(self, ingredient: str, threshold: float = 0.80) -> dict[str, Any] | None:
        """Look up an ingredient. Returns the entry dict or None.

        Tries exact match first, then fuzzy (SequenceMatcher).
        """
        norm = _normalise(ingredient)
        # Exact match
        if norm in self._index:
            return {**self._index[norm], "_match_confidence": 1.0, "_matched_name": norm}

        # Fuzzy match
        best_score = 0.0
        best_entry = None
        best_name = ""
        for name, entry in self._index.items():
            score = SequenceMatcher(None, norm, name).ratio()
            if score > best_score:
                best_score = score
                best_entry = entry
                best_name = name

        if best_score >= threshold and best_entry is not None:
            return {**best_entry, "_match_confidence": round(best_score, 3), "_matched_name": best_name}
        return None

    def lookup_all(self, ingredients: list[str]) -> list[dict[str, Any]]:
        """Look up a list of ingredients. Returns list of result dicts."""
        results = []
        for ing in ingredients:
            match = self.lookup(ing)
            results.append({
                "ingredient": ing,
                "matched": match is not None,
                "entry": match,
            })
        return results
