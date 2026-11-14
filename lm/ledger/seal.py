"""Seal a judgment exactly once.

A judgment is recorded at the moment it is made, under a protocol whose text
was fixed beforehand. Nothing sealed is ever changed: a later data revision is
logged elsewhere, a missing signal is recorded as ``unavailable`` rather than
replaced by a different signal, and a changed signal starts a new protocol.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path

from lm.ledger import chain
from lm.ledger.chain import LedgerError

RECORDS = "records.jsonl"
KINDS = ("confirmed", "provisional")


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_protocols(root):
    """Protocols that may be sealed under: status 'fixed' and document unchanged since fixing."""
    root = Path(root)
    registry = json.loads((root / "registry.json").read_text())
    usable = {}
    for entry in registry["protocols"]:
        if entry.get("status") != "fixed":
            continue
        document = root / entry["document"]
        if not document.is_file() or hashlib.sha256(document.read_bytes()).hexdigest() != entry.get("sha256"):
            raise LedgerError("protocol_document_changed_after_fixing")
        usable[entry["id"]] = entry
    return usable


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(judgment):
    if not isinstance(judgment, dict):
        raise LedgerError("judgment_not_object")
    if judgment.get("kind") not in KINDS:
        raise LedgerError("judgment_kind_invalid")
    try:
        week = date.fromisoformat(judgment.get("week", ""))
    except Exception:
        raise LedgerError("judgment_week_invalid") from None
    if week.weekday() != 2:
        raise LedgerError("judgment_week_must_be_a_wednesday")     # H.4.1 reference day
    if not isinstance(judgment.get("protocol"), str) or not judgment["protocol"]:
        raise LedgerError("judgment_protocol_missing")
    inputs = judgment.get("inputs")
    if not isinstance(inputs, dict) or not isinstance(inputs.get("sha256"), str) or len(inputs["sha256"]) != 64:
        raise LedgerError("judgment_inputs_hash_missing")
    status = judgment.get("status")
    if status == "ok":
        if not _finite(judgment.get("signal")):
            raise LedgerError("judgment_signal_invalid")
        if judgment.get("bucket") not in (1, 2, 3, 4, 5) or isinstance(judgment.get("bucket"), bool):
            raise LedgerError("judgment_bucket_invalid")
        bounds = judgment.get("bounds")
        if (not isinstance(bounds, list) or len(bounds) != 4 or not all(_finite(b) for b in bounds)
                or any(a >= b for a, b in zip(bounds, bounds[1:]))):
            raise LedgerError("judgment_bounds_invalid")
        components = judgment.get("components")
        if not isinstance(components, dict) or not components or not all(_finite(v) for v in components.values()):
            raise LedgerError("judgment_components_invalid")
        # The bucket must be the one the bounds imply; a mismatch means two code paths disagree.
        implied = 1 + sum(judgment["signal"] > b for b in bounds)
        if implied != judgment["bucket"]:
            raise LedgerError("judgment_bucket_disagrees_with_bounds")
    elif status == "unavailable":
        if not isinstance(judgment.get("reason"), str) or not judgment["reason"]:
            raise LedgerError("judgment_unavailable_needs_reason")
        if any(key in judgment for key in ("signal", "bucket", "bounds", "components")):
            raise LedgerError("judgment_unavailable_must_not_carry_a_signal")
    else:
        raise LedgerError("judgment_status_invalid")
    allowed = {"protocol", "week", "kind", "status", "signal", "bucket", "bounds", "components", "inputs", "reason"}
    if set(judgment) - allowed:
        raise LedgerError("judgment_has_unknown_fields")
    return week


def seal(directory, judgment, *, protocols, clock=None, env=None):
    """Append the judgment if it has not been sealed yet. Returns (record, None) or (None, reason)."""
    week = validate(judgment)
    if judgment["protocol"] not in protocols:
        raise LedgerError("protocol_not_fixed")
    env = os.environ if env is None else env
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        raise LedgerError("clock_must_be_timezone_aware")
    now = now.astimezone(timezone.utc)
    if now.date() < week:
        raise LedgerError("cannot_seal_a_week_that_has_not_happened")
    path = Path(directory) / RECORDS
    records = chain.read(path)
    same_week = [r for r in records if r["protocol"] == judgment["protocol"] and r["week"] == judgment["week"]]
    if any(r["kind"] == "confirmed" for r in same_week):
        # One confirmed record per week, and no provisional reading after it.
        return None, "already_confirmed"
    if judgment["kind"] == "provisional" and any(
            r["kind"] == "provisional" and r["sealed_at"][:10] == now.date().isoformat() for r in same_week):
        return None, "provisional_already_sealed_today"
    if records and stamp(now) < records[-1]["sealed_at"]:
        raise LedgerError("clock_went_backwards")
    run_id, repository = env.get("GITHUB_RUN_ID"), env.get("GITHUB_REPOSITORY")
    record = chain.append(path, {
        **judgment,
        "sealed_at": stamp(now),
        "code": {"commit": env.get("GITHUB_SHA"), "repository": repository},
        "run_url": f"https://github.com/{repository}/actions/runs/{run_id}" if run_id and repository else None,
    })
    return record, None


def record_hash(record):
    return chain.line_hash(chain.canonical(record))
