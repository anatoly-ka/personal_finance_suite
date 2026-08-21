"""Reconstruct a transaction's group (קבוצת ענפים) and coarse bucket from its
category (ענף), using category_map.yaml.

The ledger produced by finance-unify does NOT store category_group; it is a
strict function of the category, so it is rebuilt here on demand (used by the
analysis skill, Skill 3).

category_map.yaml structure:

    category_to_group:            # the export's own taxonomy (ענף -> קבוצת ענפים)
      "<category>": "<group>"
    group_to_bucket:              # your coarse buckets (edit the right-hand side)
      "<group>": "<bucket>"       # defaults to identity
    category_to_bucket:           # optional per-category overrides
      "<category>": "<bucket>"

Loading degrades gracefully if PyYAML is not installed (returns empty maps).
"""

from __future__ import annotations

import os

_DATA = os.path.join(os.path.dirname(__file__), "data", "category_map.yaml")


def load(path: str | None = None) -> dict:
    path = path or _DATA
    if not path or not os.path.exists(path):
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data.setdefault("category_to_group", {})
    data.setdefault("group_to_bucket", {})
    data.setdefault("category_to_bucket", {})
    return data


def group_for(category: str, mapping: dict) -> str:
    """Reconstruct the group (קבוצת ענפים) for a category (ענף)."""
    return mapping.get("category_to_group", {}).get(category, "")


def bucket_for(category: str, mapping: dict) -> str:
    """Coarse bucket for a category: a per-category override wins, else the
    bucket of its group, else the group name itself, else ''."""
    cat_override = mapping.get("category_to_bucket", {})
    if category in cat_override:
        return cat_override[category]
    group = group_for(category, mapping)
    g2b = mapping.get("group_to_bucket", {})
    if group in g2b:
        return g2b[group]
    return group
