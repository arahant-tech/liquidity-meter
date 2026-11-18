"""The weekly judgment for protocol tilt-v1, built from the measurement and nothing else.

Signal: the 13-week change in reserves as a share of total assets 13 weeks
earlier. Bucket: its quintile against strictly earlier weeks. Tilt: buckets 4
and 5. The numbers that are sealed are rounded first, and the sealed bucket is
taken from those rounded numbers, so a record always agrees with itself.

No price is read here. Prices enter only when a sealed judgment is scored.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from lm.ledger.chain import LedgerError
from lm.measure import quadrant
from lm.store import history

PROTOCOL = quadrant.PROTOCOL
DIGITS = 4


def bucket_of(value, bounds):
    return 1 + sum(value > b for b in bounds)


def build(history_root):
    """The judgment for the latest complete H.4.1 week. Raises LedgerError when it cannot be made."""
    root = Path(history_root)
    series, digest = {}, hashlib.sha256()
    for key, sid in sorted(quadrant.SERIES.items(), key=lambda item: item[1]):
        path = root / "series" / (sid + ".csv")
        if not path.is_file():
            raise LedgerError("judgment_input_missing")
        data = path.read_bytes()
        digest.update(sid.encode() + b"\n" + data)
        try:
            series[key] = {day: float(value) for day, value in history.decode(data) if value is not None}
        except ValueError:
            raise LedgerError("judgment_input_invalid") from None
    rows = quadrant.assign_buckets([r for r in quadrant.weekly_rows(series) if r["date"] >= quadrant.START])
    quadrant.assign_buckets(rows, "x", "xbucket", "xbounds")
    if not rows:
        raise LedgerError("judgment_no_complete_week")
    last = rows[-1]
    base = {"protocol": PROTOCOL, "week": last["date"], "kind": "confirmed",
            "inputs": {"sha256": digest.hexdigest(), "series": sorted(quadrant.SERIES.values())}}
    if last["bounds"] is None:
        return {**base, "status": "unavailable", "reason": "not_enough_history_for_a_bucket"}
    signal, bounds = round(last["g"], DIGITS), [round(b, DIGITS) for b in last["bounds"]]
    if any(a >= b for a, b in zip(bounds, bounds[1:])):
        return {**base, "status": "unavailable", "reason": "bucket_bounds_not_distinct"}
    bucket = bucket_of(signal, bounds)
    components = {"s": round(last["s"], DIGITS), "v": round(last["v"], DIGITS), "x": round(last["x"], DIGITS),
                  "tilt": int(bucket >= quadrant.TILT_FROM_BUCKET)}
    if last["xbounds"] is not None:
        x_bucket = bucket_of(components["x"], [round(b, DIGITS) for b in last["xbounds"]])
        components.update(x_bucket=x_bucket, x_tilt=int(x_bucket >= quadrant.TILT_FROM_BUCKET))
    if last["rrp_share_mean"] is not None:
        components["rrp_share_mean"] = round(last["rrp_share_mean"], DIGITS)
    return {**base, "status": "ok", "signal": signal, "bucket": bucket, "bounds": bounds, "components": components}
