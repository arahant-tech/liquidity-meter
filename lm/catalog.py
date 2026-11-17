"""Single registry of providers and series.

Every provider list, FRED allow-list and label table in this project is either
derived from ``catalog/series.json`` at import time or pinned to it by a test.
Add a source here first; nothing else should need a second hand-typed copy.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "catalog" / "series.json"


def load(path=PATH):
    content = json.loads(Path(path).read_text())
    if content.get("schema_version") != 1:
        raise ValueError("unsupported_catalog_schema")
    ids = [p["id"] for p in content["providers"]]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("empty_or_duplicate_provider_catalog")
    series = [s["series_id"] for s in content["fred"]["series"]]
    if len(series) != len(set(series)) or not series:
        raise ValueError("empty_or_duplicate_series_catalog")
    return content


def providers(path=PATH):
    """Providers in collection order."""
    return load(path)["providers"]


def fred(path=PATH):
    """The FRED section: scope, series specs, assumptions and breaks."""
    return load(path)["fred"]


def history(path=PATH):
    """Model-input histories: observation start and the series to keep in full."""
    section = load(path)["history"]
    ids = [s["series_id"] for s in section["series"]]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("empty_or_duplicate_history_catalog")
    return section
