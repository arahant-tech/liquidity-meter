"""Append-only JSON-lines files where every line commits to the one before it.

Each line is a canonical JSON object carrying ``seq`` (its position) and
``prev`` (the SHA-256 of the previous line's exact bytes). Changing, removing
or reordering any earlier line breaks every hash after it, so the last line's
hash stands for the whole file. That hash is what gets attested outside the
repository.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class LedgerError(ValueError):
    """A fixed error code; never free text."""


def canonical(record):
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def line_hash(line):
    return hashlib.sha256(line.encode()).hexdigest()


def parse(data):
    """Verify a whole chain file and return its records. Raises on any inconsistency."""
    if not data:
        return []
    text = data.decode()
    if not text.endswith("\n"):
        raise LedgerError("chain_missing_final_newline")
    records, previous = [], None
    for index, line in enumerate(text[:-1].split("\n")):
        try:
            record = json.loads(line)
        except Exception:
            raise LedgerError("chain_line_not_json") from None
        if not isinstance(record, dict) or canonical(record) != line:
            raise LedgerError("chain_line_not_canonical")
        if record.get("schema_version") != 1:
            raise LedgerError("chain_unsupported_schema")
        if record.get("seq") != index:
            raise LedgerError("chain_sequence_broken")
        if record.get("prev") != previous:
            raise LedgerError("chain_hash_broken")
        previous = line_hash(line)
        records.append(record)
    return records


def read(path):
    path = Path(path)
    return parse(path.read_bytes()) if path.exists() else []


def head(path):
    """Hash of the last line: the commitment to everything recorded so far."""
    path = Path(path)
    if not path.exists() or not path.read_bytes():
        return None
    parse(path.read_bytes())
    return line_hash(path.read_bytes().decode()[:-1].split("\n")[-1])


def append(path, record):
    """Add one record. The existing file is verified first; a broken chain is never extended."""
    path = Path(path)
    existing = read(path)
    if "seq" in record or "prev" in record:
        raise LedgerError("record_must_not_set_chain_fields")
    stored = {**record, "schema_version": 1, "seq": len(existing), "prev": head(path)}
    line = canonical(stored)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write((line + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    return stored


def is_append_only(before, after):
    """True when ``after`` is ``before`` plus whole new lines, and still a valid chain."""
    if not after.startswith(before):
        return False
    try:
        parse(after)
    except LedgerError:
        return False
    return True
