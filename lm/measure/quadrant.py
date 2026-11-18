"""Scalar × vector map of Federal Reserve liquidity.

The Fed's balance sheet is an identity: total assets L equal reserve balances R
plus everything that is not reserves N (reverse repos, the Treasury's account,
currency, other). Over a 13-week window, scaled by total assets at its start:

    scalar  s = ΔL / L₀      how much the total grew
    vector  v = −ΔN / L₀     how much parked money moved into reserves
    g = s + v = ΔR / L₀      the change in reserves, exactly

Signs of s and v give four quadrants; the diagonal s + v = 0 separates weeks
when reserves rose from weeks when they fell, splitting two of the quadrants.
That makes six cells.

The judgment is one number: the 13-week change in reserves, g, placed in a
quintile of its own past. Buckets 4 and 5 mean "tilt toward growth stocks";
anything else means "no tilt". Scalar and vector only explain where g came
from. A candidate, g with the Treasury account's contribution removed, is
reported next to it so the two can be compared as they are recorded.

This module only measures. It reads Wednesday H.4.1 levels from the history
store and writes the map. It never reads prices and makes no forecast.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from lm.collect import live_api
from lm.store import history

SERIES = {"L": "WALCL", "R": "WRBWFRBL", "RRP": "WLRRAOL", "FRRP": "WLRRAFOIAL", "TGA": "WDTGAL", "CUR": "WCURCIR"}
WINDOW_WEEKS = 13
START = "2009-01-01"          # reserves became a meaningful share of the balance sheet after 2008
MIN_HISTORY = 156             # weeks of past values before a bucket is assigned
STALE_AFTER_DAYS = 14
CELLS = ("A", "B+", "B-", "C+", "C-", "D")
PROTOCOL = "tilt-v1"          # research/protocols/PROTOCOL_tilt_v1.md
TILT_FROM_BUCKET = 4          # buckets 4 and 5 tilt toward growth
TOTAL_LINE = 1.0              # watch: total assets growing by more than this (% over 13 weeks)
RRP_SHARE_LINE = 1.0          # watch: domestic reverse repos above this share of total assets (%)
RRP_MEAN_WEEKS = 4            # quarter-end spikes are averaged out


def classify(s, v):
    """One of six cells. Zero counts as 'not up' for both axes and for reserves."""
    g = s + v
    if s > 0 and v > 0:
        return "A"                       # total up, direction up: reserves must rise
    if s > 0:
        return "B+" if g > 0 else "B-"   # total up, direction down: which one wins
    if v > 0:
        return "C+" if g > 0 else "C-"   # total down, direction up: which one wins
    return "D"                           # total down, direction down: reserves must fall


def quantile(sorted_values, q):
    """Linear interpolation between order statistics (the common default)."""
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (position - low) * (sorted_values[high] - sorted_values[low])


def weekly_rows(series):
    """Rows for every Wednesday where all six lines exist now and exactly 13 weeks earlier.

    ``series`` maps the short names in SERIES to {iso_date: value in millions of dollars}.
    A missing value is never filled; that week is simply not on the map.
    """
    rows = []
    for day in sorted(series["L"]):
        earlier = (date.fromisoformat(day) - timedelta(weeks=WINDOW_WEEKS)).isoformat()
        if any(series[k].get(day) is None or series[k].get(earlier) is None for k in SERIES):
            continue
        now = {k: series[k][day] / 1e3 for k in SERIES}             # billions
        then = {k: series[k][earlier] / 1e3 for k in SERIES}
        base = then["L"]
        if base <= 0:
            continue
        change = {k: now[k] - then[k] for k in SERIES}
        s = 100 * change["L"] / base
        g = 100 * change["R"] / base
        v = g - s                                                  # = −Δ(L − R) / L₀
        sources = {"rrp": -100 * change["RRP"] / base, "frrp": -100 * change["FRRP"] / base,
                   "tga": -100 * change["TGA"] / base, "cur": -100 * change["CUR"] / base}
        sources["other"] = v - sum(sources.values())
        rows.append({"date": day, "s": s, "v": v, "g": g, "cell": classify(s, v), "levels": now, "sources": sources,
                     # reserves had the Treasury account not moved: g + ΔTGA / L₀
                     "x": g - sources["tga"], "rrp_share": 100 * now["RRP"] / now["L"] if now["L"] > 0 else None})
    for i, row in enumerate(rows):
        recent = rows[max(0, i - RRP_MEAN_WEEKS + 1):i + 1]
        complete = (len(recent) == RRP_MEAN_WEEKS and all(r["rrp_share"] is not None for r in recent)
                    and recent[0]["date"] == (date.fromisoformat(row["date"]) - timedelta(weeks=RRP_MEAN_WEEKS - 1)).isoformat())
        row["rrp_share_mean"] = sum(r["rrp_share"] for r in recent) / RRP_MEAN_WEEKS if complete else None
    return rows


def assign_buckets(rows, value="g", bucket="bucket", bounds="bounds"):
    """Quintile of a value against strictly earlier weeks only; None until enough history exists."""
    past = []
    for row in rows:
        if len(past) >= MIN_HISTORY:
            ordered = sorted(past)
            edges = [quantile(ordered, q) for q in (.2, .4, .6, .8)]
            row[bucket] = 1 + sum(b < row[value] for b in edges)
            row[bounds] = edges
        else:
            row[bucket], row[bounds] = None, None
        past.append(row[value])
    return rows


def tilt(bucket):
    """'growth' in buckets 4 and 5, 'neutral' below, None while there is no bucket yet."""
    if bucket is None:
        return None
    return "growth" if bucket >= TILT_FROM_BUCKET else "neutral"


def since(rows, state):
    """First week of the unbroken run of weeks that share the latest week's state."""
    start = rows[-1]["date"]
    for row in reversed(rows[:-1]):
        if state(row) != state(rows[-1]):
            break
        start = row["date"]
    return start


def judgment(rows):
    """The single judgment, its candidate, where the change came from, and the two watch items."""
    last = rows[-1]

    def reading(value, bucket, bounds):
        edges = last[bounds]
        return {"value": round(last[value], 4), "bucket": last[bucket],
                "bounds": None if edges is None else [round(b, 4) for b in edges],
                "tilt_from": None if edges is None else round(edges[TILT_FROM_BUCKET - 2], 4),
                "tilt": tilt(last[bucket]), "since": since(rows, lambda r: tilt(r[bucket]))}

    main, candidate = reading("g", "bucket", "bounds"), reading("x", "xbucket", "xbounds")
    if last["g"] <= 0:
        driver = "none"
    elif last["s"] > 0 and last["v"] > 0:
        driver = "both"
    else:
        driver = "total" if last["s"] > 0 else "direction"
    mean = last["rrp_share_mean"]
    return {
        "protocol": PROTOCOL, "tilt": main["tilt"], "since": main["since"],
        "indicator": {"id": "reserve_change_13w", **main},
        "candidate": {"id": "reserve_change_ex_tga_13w", **candidate},
        "agree": None if main["tilt"] is None or candidate["tilt"] is None else main["tilt"] == candidate["tilt"],
        "source": {"driver": driver, "total": round(last["s"], 4), "direction": round(last["v"], 4),
                   "tga": round(last["sources"]["tga"], 4)},
    }, {
        "total_assets": {"value": round(last["s"], 4), "line": TOTAL_LINE, "above": last["s"] > TOTAL_LINE,
                         "since": since(rows, lambda r: r["s"] > TOTAL_LINE)},
        "reverse_repo": {"billion_usd": round(last["levels"]["RRP"], 3), "share": round(last["rrp_share"], 4),
                         "share_mean": None if mean is None else round(mean, 4), "mean_weeks": RRP_MEAN_WEEKS,
                         "line": RRP_SHARE_LINE, "above": None if mean is None else mean >= RRP_SHARE_LINE,
                         "since": since(rows, lambda r: None if r["rrp_share_mean"] is None
                                        else r["rrp_share_mean"] >= RRP_SHARE_LINE)},
    }


def _retained(output, reason, checked_at):
    """Keep the last good map, clearly marked, rather than publish a blank or a guess."""
    if output is not None and output.exists():
        try:
            previous = json.loads(output.read_text())
        except Exception:
            previous = None
        if isinstance(previous, dict) and previous.get("points"):
            return {**previous, "status": "retained", "reason": reason, "checked_at": checked_at}
    return {"schema_version": 1, "status": "unavailable", "reason": reason, "generated_at": checked_at,
            "checked_at": checked_at, "asof": None, "now": None, "points": [], "cells": {}, "inputs": {}}


def build(history_root, *, clock=None, previous_output=None):
    """The public map. Any missing input yields 'retained' or 'unavailable', never a partial map."""
    root = Path(history_root)
    now = live_api._utc(clock)
    stamp = live_api._stamp(now)
    index = {}
    if (root / "index.json").exists():
        try:
            index = json.loads((root / "index.json").read_text()).get("series", {})
        except Exception:
            return _retained(previous_output, "history_index_invalid", stamp)
    series = {}
    for key, sid in SERIES.items():
        stored = history.read_series(root, root, sid, True)
        if not stored:
            return _retained(previous_output, "input_missing_" + sid, stamp)
        try:
            series[key] = {day: float(value) for day, value in stored if value is not None}
        except ValueError:
            return _retained(previous_output, "input_invalid_" + sid, stamp)
    rows = assign_buckets([r for r in weekly_rows(series) if r["date"] >= START])
    assign_buckets(rows, "x", "xbucket", "xbounds")
    if not rows:
        return _retained(previous_output, "no_complete_week", stamp)
    last = rows[-1]
    if date.fromisoformat(last["date"]) > now.date():
        return _retained(previous_output, "future_observation_rejected", stamp)
    age = (now.date() - date.fromisoformat(last["date"])).days
    counts = {c: sum(r["cell"] == c for r in rows) for c in CELLS}
    judged, watch = judgment(rows)
    return {
        "schema_version": 1,
        "status": "stale" if age > STALE_AFTER_DAYS else "ok",
        "reason": "latest_week_older_than_%d_days" % STALE_AFTER_DAYS if age > STALE_AFTER_DAYS else None,
        "generated_at": stamp, "checked_at": stamp, "asof": last["date"],
        "definition": {"window_weeks": WINDOW_WEEKS, "unit": "percent of total assets 13 weeks earlier",
                       "scalar": "change in total assets (WALCL)",
                       "vector": "minus the change in non-reserve liabilities (WALCL − WRBWFRBL)",
                       "identity": "s + v = change in reserve balances (WRBWFRBL)",
                       "bucket": "quintile of s + v against strictly earlier weeks since %s" % START,
                       "judgment": "tilt toward growth when the bucket is %d or higher, otherwise no tilt" % TILT_FROM_BUCKET,
                       "candidate": "x = s + v with the Treasury account's contribution removed; own quintiles"},
        "vintage": "current_as_of_retrieval",
        "now": {"date": last["date"], "s": round(last["s"], 4), "v": round(last["v"], 4), "g": round(last["g"], 4),
                "cell": last["cell"], "bucket": last["bucket"],
                "bounds": None if last["bounds"] is None else [round(b, 4) for b in last["bounds"]],
                "levels_billion_usd": {k: round(x, 3) for k, x in last["levels"].items()},
                "vector_sources": {k: round(x, 4) for k, x in last["sources"].items()}},
        "judgment": judged, "watch": watch,
        "cells": {c: {"weeks": counts[c], "share": round(counts[c] / len(rows), 4)} for c in CELLS},
        "points": [{"d": r["date"], "s": round(r["s"], 3), "v": round(r["v"], 3), "c": r["cell"], "b": r["bucket"],
                    "x": round(r["x"], 3), "xb": r["xbucket"]} for r in rows],
        "inputs": {sid: {k: index.get(sid, {}).get(k) for k in ("last_date", "retrieved_at", "status")} for sid in SERIES.values()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--history", type=Path, required=True, help="History store root, e.g. data/history")
    parser.add_argument("--output", type=Path, required=True, help="Public map, e.g. data/public/quadrant.json")
    args = parser.parse_args(argv)
    result = build(args.history, previous_output=args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    live_api._atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "reason": result.get("reason"), "asof": result.get("asof"),
                      "weeks": len(result.get("points", [])), "cell": (result.get("now") or {}).get("cell"),
                      "tilt": (result.get("judgment") or {}).get("tilt")}, ensure_ascii=False))
    return 0 if result["status"] in ("ok", "stale") else 2


if __name__ == "__main__":
    raise SystemExit(main())
