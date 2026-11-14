"""Score sealed judgments against what happened afterwards.

The entry price is the first observation dated strictly after the day the
judgment was sealed: the first price someone following the record could have
traded at. Back-tests that align a signal to its reference Wednesday assume
the reader knew it before it was published; this does not.

Price series are copyrighted, so a score keeps the return and the two dates,
never the price levels.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import statistics

from lm.ledger import chain
from lm.ledger.chain import LedgerError
from lm.ledger.seal import RECORDS, record_hash, stamp

SCORES = "scores.jsonl"


def entry_exit(rows, sealed_at, horizon_weeks):
    """(entry_row, exit_row) or None while the horizon has not been observed yet.

    ``rows`` is [(iso_date, value_text_or_None)] in ascending order.
    """
    sealed_day = sealed_at[:10]
    entry = next(((d, v) for d, v in rows if d > sealed_day and v is not None), None)
    if entry is None:
        return None
    target = (date.fromisoformat(entry[0]) + timedelta(days=7 * horizon_weeks)).isoformat()
    exit_row = next(((d, v) for d, v in rows if d >= target and v is not None), None)
    if exit_row is None:
        return None
    return entry, exit_row


def due(directory, prices, plan, *, clock=None):
    """Append a score for every confirmed judgment whose horizon is now observable.

    ``prices`` maps asset id to its rows; ``plan`` is the protocol's list of
    {"asset", "horizon_weeks", "role"}. Returns the scores added.
    """
    directory = Path(directory)
    records = chain.read(directory / RECORDS)
    scores = chain.read(directory / SCORES)
    done = {(s["record"], s["asset"], s["horizon_weeks"]) for s in scores}
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    added = []
    for record in records:
        if record["kind"] != "confirmed" or record["status"] != "ok":
            continue
        reference = record_hash(record)
        for item in plan.get(record["protocol"], []):
            key = (reference, item["asset"], item["horizon_weeks"])
            rows = prices.get(item["asset"])
            if key in done or not rows:
                continue
            found = entry_exit(rows, record["sealed_at"], item["horizon_weeks"])
            if found is None:
                continue
            (entry_day, entry_value), (exit_day, exit_value) = found
            start, end = float(entry_value), float(exit_value)
            if not (math.isfinite(start) and math.isfinite(end)) or start <= 0:
                raise LedgerError("price_not_usable")
            evidence = hashlib.sha256(f"{entry_day},{entry_value}\n{exit_day},{exit_value}\n".encode()).hexdigest()
            added.append(chain.append(directory / SCORES, {
                "record": reference, "protocol": record["protocol"], "week": record["week"],
                "bucket": record["bucket"], "asset": item["asset"], "role": item["role"],
                "horizon_weeks": item["horizon_weeks"],
                "entry_date": entry_day, "exit_date": exit_day,
                "entry_lag_days": (date.fromisoformat(entry_day) - date.fromisoformat(record["sealed_at"][:10])).days,
                "return": round(end / start - 1, 6),
                "price_rows_sha256": evidence, "scored_at": stamp(now),
            }))
            done.add(key)
    return added


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1          # average rank for ties
        i = j + 1
    return ranks


def rank_correlation(x, y):
    """Spearman's rho, or None when either side has no variation."""
    if len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def shift_null(buckets, returns):
    """What the rank correlation looks like when the call order is slid against the outcomes.

    Circular shifts keep the week-to-week persistence of both series, which a
    plain shuffle would destroy. Returns (sorted correlations, share at least as
    large as the observed one), or None when there are too few to say anything.
    """
    observed = rank_correlation(buckets, returns)
    if observed is None:
        return None
    values = [rank_correlation(buckets[s:] + buckets[:s], returns) for s in range(1, len(buckets))]
    values = sorted(v for v in values if v is not None)
    if len(values) < 19:
        return None
    return values, sum(v >= observed for v in values) / len(values)


def scorecard(directory, plan, *, min_independent=6, clock=None):
    """A summary for the site. It reports counts and spreads; it does not declare a verdict."""
    directory = Path(directory)
    records = chain.read(directory / RECORDS)
    scores = chain.read(directory / SCORES)
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    out = {"schema_version": 1, "generated_at": stamp(now),
           "head": {"records": chain.head(directory / RECORDS), "scores": chain.head(directory / SCORES)},
           "protocols": {}}
    for protocol, items in plan.items():
        mine = [r for r in records if r["protocol"] == protocol]
        confirmed = [r for r in mine if r["kind"] == "confirmed"]
        usable = [r for r in confirmed if r["status"] == "ok"]
        summary = {"confirmed": len(confirmed), "provisional": sum(r["kind"] == "provisional" for r in mine),
                   "unavailable": len(confirmed) - len(usable),
                   "first_week": confirmed[0]["week"] if confirmed else None,
                   "last_week": confirmed[-1]["week"] if confirmed else None,
                   "latest": ({k: usable[-1][k] for k in ("week", "bucket", "signal", "sealed_at")} if usable else None),
                   "assets": []}
        for item in items:
            rows = sorted((s for s in scores if s["protocol"] == protocol and s["asset"] == item["asset"]
                           and s["horizon_weeks"] == item["horizon_weeks"]), key=lambda s: s["week"])
            buckets, returns = [s["bucket"] for s in rows], [s["return"] for s in rows]
            independent = len(rows) // item["horizon_weeks"]     # weekly calls over an h-week horizon overlap
            by_bucket = {}
            for bucket in (1, 2, 3, 4, 5):
                values = [r for b, r in zip(buckets, returns) if b == bucket]
                by_bucket[str(bucket)] = ({"n": len(values), "mean": round(statistics.fmean(values), 6),
                                           "median": round(statistics.median(values), 6),
                                           "positive_share": round(sum(v > 0 for v in values) / len(values), 4)}
                                          if values else {"n": 0})
            enough = independent >= min_independent
            asset = {"asset": item["asset"], "role": item["role"], "horizon_weeks": item["horizon_weeks"],
                     "scored": len(rows), "pending": len(usable) - len(rows), "independent": independent,
                     "enough_to_read": enough, "by_bucket": by_bucket,
                     "rank_correlation": None, "shift_null": None}
            if enough:
                rho = rank_correlation(buckets, returns)
                asset["rank_correlation"] = None if rho is None else round(rho, 4)
                null = shift_null(buckets, returns)
                if null:
                    values, share = null
                    asset["shift_null"] = {"shifts": len(values),
                                           "p05": round(values[int(.05 * (len(values) - 1))], 4),
                                           "p95": round(values[int(.95 * (len(values) - 1))], 4),
                                           "share_at_least_observed": round(share, 4)}
            summary["assets"].append(asset)
        out["protocols"][protocol] = summary
    return out
