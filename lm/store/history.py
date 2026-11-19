"""Full native histories for the measurement layer, with a forward revision log.

The live poller keeps only the latest few observations. The model needs every
observation since 2002, so this store fetches each registered series in full,
keeps the current vintage as one CSV per series and appends every later change
to ``revisions.jsonl``. From the first run on, that log is a real record of
what was known when; it does not reconstruct vintages before the first run.

Series marked ``persist: false`` in the registry are copyrighted on FRED. Their
values are written only to a private runtime directory that is never committed
or uploaded; the public index keeps just a content hash and the last date.
"""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from lm import catalog
from lm.collect import live_api
from lm.collect.live_api import FredClient, FredError

LIMIT = 100000                      # FRED's maximum page; a full page means truncation
RESTRICTED_NOTE = re.compile(r"reproduction[^.]{0,80}prohibited|all rights reserved|pre-approval required", re.I)
HEADER = "date,value\n"


def _atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _number(text):
    """Compare values as numbers so '1.50' and '1.5' are not a revision."""
    if text in ("", None):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def encode(rows):
    """Canonical CSV bytes: one 'date,value' line per observation, missing value empty."""
    return (HEADER + "".join(f"{day},{'' if value is None else value}\n" for day, value in rows)).encode()


def decode(data):
    lines = data.decode().split("\n")
    if lines[0] + "\n" != HEADER:
        raise ValueError("unexpected_history_header")
    rows = []
    for line in lines[1:]:
        if line:
            day, _, value = line.partition(",")
            rows.append((day, value or None))
    return rows


def validated(spec_id, metadata_body, observation_body, as_of):
    """Identity, order and finiteness checks. Nothing is imputed or reordered silently."""
    metadata = metadata_body.get("seriess", [])
    if len(metadata) != 1 or metadata[0].get("id") != spec_id:
        raise FredError("metadata_identity_mismatch")
    metadata = metadata[0]
    if not metadata.get("frequency") or not metadata.get("units"):
        raise FredError("metadata_incomplete")
    rows = observation_body.get("observations")
    if not isinstance(rows, list) or not rows:
        raise FredError("no_observations")
    if len(rows) >= LIMIT:
        raise FredError("history_truncated")
    out, previous = [], ""
    for row in rows:
        day = row.get("date", "")
        try:
            parsed = date.fromisoformat(day)
        except Exception:
            raise FredError("invalid_observation_date") from None
        if parsed > as_of:
            raise FredError("future_observation_rejected")
        if day <= previous:
            raise FredError("observations_not_strictly_ascending")
        previous = day
        value = str(row.get("value", "."))
        if value in ("", "."):
            out.append((day, None))
            continue
        number = _number(value)
        if number is None or not number.is_finite():
            raise FredError("invalid_observation_value")
        out.append((day, value))
    if all(value is None for _, value in out):
        raise FredError("no_valid_observation")
    return metadata, out


def changes(old_rows, new_rows):
    """Differences that rewrite the past. New observations at the end are not revisions."""
    old, new = dict(old_rows), dict(new_rows)
    last_known = max(old) if old else ""
    found = []
    for day in sorted(set(old) | set(new)):
        if day in old and day in new:
            if _number(old[day]) != _number(new[day]):
                found.append({"date": day, "kind": "revised", "old": old[day], "new": new[day]})
        elif day in old:
            found.append({"date": day, "kind": "removed", "old": old[day], "new": None})
        elif day < last_known:
            found.append({"date": day, "kind": "added_late", "old": None, "new": new[day]})
    return found


def _paths(root, runtime, series_id, persist):
    base = Path(root) if persist else Path(runtime)
    return base / "series" / (series_id + ".csv")


def read_series(root, runtime, series_id, persist=True):
    """Stored observations as [(date, value-or-None)], or None if not stored."""
    path = _paths(root, runtime, series_id, persist)
    return decode(path.read_bytes()) if path.is_file() else None


def refresh(root, runtime, *, client=None, clock=None, section=None, max_seconds=240):
    """Bring every registered history up to date. One failing series never blocks the rest."""
    root, runtime = Path(root), Path(runtime)
    for target in (root, runtime):
        live_api._validate_output_root(target)
    section = section or catalog.history()
    specs = section["series"]
    start = section["observation_start"]
    credential_error = False
    if client is None:
        try:
            key, _ = live_api.load_credential()
            # One request per second: with the live poller's requests earlier in the
            # same run this stays under FRED's 120 requests per minute.
            client = FredClient(key, throttle_seconds=1.0, timeout=30,
                                allowed=[s["series_id"] for s in specs])
        except Exception:
            credential_error = True
    started = live_api._utc(clock)
    day = live_api.fred_day(started).isoformat()          # FRED's own date, not UTC's (see fred_day)
    index_path = root / "index.json"
    previous = {}
    if index_path.exists():
        try:
            content = json.loads(index_path.read_text())
            if content.get("schema_version") != 1 or not isinstance(content.get("series"), dict):
                raise ValueError()
            previous = content["series"]
        except Exception:
            # Never replace a damaged index with a blank one that looks fresh.
            raise RuntimeError("previous_history_index_invalid") from None
    if client is not None:
        client.deadline = time.monotonic() + max_seconds
    entries, revised = {}, []
    for spec in specs:
        sid, persist = spec["series_id"], bool(spec["persist"])
        old = previous.get(sid, {})
        checked_at = live_api._stamp(live_api._utc(clock))
        path = _paths(root, runtime, sid, persist)
        if not persist:
            # A series that is no longer allowed in the public store must not linger there.
            _paths(root, runtime, sid, True).unlink(missing_ok=True)
        try:
            if credential_error:
                raise FredError("credential_unavailable")
            params = {"series_id": sid, "realtime_start": day, "realtime_end": day}
            metadata_body = client.request("series", params)
            listed = metadata_body.get("seriess") or [{}]
            if persist and RESTRICTED_NOTE.search(str(listed[0].get("notes", ""))):
                raise FredError("restricted_series_cannot_be_persisted")
            updated = listed[0].get("last_updated")
            if (old.get("sha256") and old.get("source_last_updated") == updated and updated
                    and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == old["sha256"]):
                entries[sid] = {**old, "status": "ok", "error": None, "checked_at": checked_at, "changed": False}
                continue
            observation_body = client.request("series/observations", {
                **params, "observation_start": start, "observation_end": day,
                "sort_order": "asc", "units": "lin", "output_type": 1, "limit": LIMIT})
            metadata, rows = validated(sid, metadata_body, observation_body, started.date())
            if old.get("units") and old["units"] != metadata["units"]:
                raise FredError("metadata_units_changed")
            data = encode(rows)
            digest = hashlib.sha256(data).hexdigest()
            retrieved_at = live_api._stamp(live_api._utc(clock))
            content_changed = digest != old.get("sha256")
            if persist and content_changed and path.is_file():
                for item in changes(decode(path.read_bytes()), rows):
                    revised.append({"series_id": sid, **item, "detected_at": retrieved_at,
                                    "source_last_updated": updated,
                                    "previous_source_last_updated": old.get("source_last_updated")})
            if content_changed or not path.is_file():
                _atomic_bytes(path, data)
            valid = [(d, v) for d, v in rows if v is not None]
            entries[sid] = {
                "series_id": sid, "status": "ok", "error": None, "persisted": persist,
                "checked_at": retrieved_at, "changed": content_changed,
                # When this exact content was first obtained here; not the publication time.
                "retrieved_at": retrieved_at if content_changed else old.get("retrieved_at", retrieved_at),
                "first_retrieved_at": old.get("first_retrieved_at", retrieved_at),
                "source_last_updated": updated, "units": metadata["units"],
                "frequency": metadata["frequency"], "observations": len(rows),
                "first_date": rows[0][0], "last_date": valid[-1][0],
                "sha256": digest, "source_url": "https://fred.stlouisfed.org/series/" + sid,
                "vintage": "current_as_of_retrieval",
                # For the owner's audit: persisted series whose FRED notes mention copyright.
                "copyright_notice": bool(re.search(r"copyright", str(metadata.get("notes", "")), re.I)),
            }
            if persist:
                entries[sid]["last_value"] = valid[-1][1]
        except Exception as error:
            code = str(error) if isinstance(error, FredError) else type(error).__name__
            if client is not None and client.key:
                code = code.replace(client.key, "[redacted]")
            if not re.fullmatch(r"[A-Za-z0-9_]+", code):
                code = "fetch_failed"
            # Keep the last good description; the stored file is left untouched.
            entries[sid] = {**old, "series_id": sid, "persisted": persist, "status": "error",
                            "error": code, "checked_at": checked_at, "changed": False}
    if revised:
        with (root / "revisions.jsonl").open("a") as handle:
            for item in revised:
                handle.write(json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    errors = sum(e["status"] == "error" for e in entries.values())
    index = {"schema_version": 1, "generated_at": live_api._stamp(live_api._utc(clock)),
             "observation_start": start, "vintage_policy": "current_as_of_retrieval_with_forward_revision_log",
             "outcome": "error" if errors == len(entries) else "partial" if errors else "ok",
             "series": entries}
    root.mkdir(parents=True, exist_ok=True)
    live_api._atomic_json(index_path, index)
    return {"outcome": index["outcome"], "total": len(entries), "errors": errors,
            "changed": sum(bool(e.get("changed")) for e in entries.values()),
            "revisions": len(revised), "generated_at": index["generated_at"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Persisted store, e.g. data/history")
    parser.add_argument("--runtime", type=Path, required=True,
                        help="Private runtime directory for series that must not be persisted")
    parser.add_argument("--max-seconds", type=int, default=240)
    args = parser.parse_args(argv)
    result = refresh(args.root, args.runtime, max_seconds=args.max_seconds)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["outcome"] != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
