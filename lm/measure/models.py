"""Provisional model board: which of the pre-declared rules are on this week.

The seventeen fixed rules of ``research/studies/models-2026-11``: eight read the
Fed's balance sheet (the quadrant), six read rates, spreads, the dollar, implied
volatility and money supply, and three combine them. Seven of them form the
"condition score".

This module only reports each rule's state. It never reads the prices of the
assets the rules were tested on, and it makes no forecast; what the rules were
worth in the past is in the study's fixed results, shown with its evidence grade.

Two inputs (Moody's spread, Cboe VIX) are copyrighted. They are read from the
private runtime store, and only on/off states derived from them are written.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from datetime import date, timedelta
import json
from pathlib import Path
from statistics import median

from lm.collect import live_api
from lm.store import history

PUBLIC = {"L": "WALCL", "R": "WRBWFRBL", "TGA": "WDTGAL", "RRP": "WLRRAOL",
          "REAL": "DFII10", "USD": "DTWEXBGS", "FFR": "DFEDTARU", "M2": "M2SL"}
PRIVATE = {"BAA": "BAA10Y", "VIX": "VIXCLS"}
WEEKLY = ("L", "R", "TGA", "RRP")
DAILY = ("REAL", "USD", "FFR", "BAA", "VIX")
WINDOW_WEEKS = 13
FED_WINDOW_WEEKS = 26
VIX_WEEKS = 52
ACCEL_WEEKS = 4
M2_LAG_DAYS = 35              # monthly M2 is usable this long after its month ends
STALE_AFTER_DAYS = 14
HISTORY_WEEKS = 52
STUDY = "models-2026-11"
VOTERS = ("Q1", "Q2", "Q7", "F1", "F2", "F3", "F4")
NAMES = {
    "Q1": "준비금 증가", "Q2": "방향 양", "Q3": "총량 양", "Q4": "총량↓ 방향↑",
    "Q5": "총량↑·준비금↓ 회피", "Q6": "준비금 가속", "Q7": "재무부 현금 제외", "Q8": "통설 순유동성",
    "F1": "신용 스프레드 축소", "F2": "실질금리 하락", "F3": "달러 약세",
    "F4": "변동성 낮음", "F5": "연준 비긴축", "F6": "M2 증가",
    "C1": "다수결", "C2": "점수 비례", "C3": "둘 다 나쁠 때만 회피",
}
RULES = tuple(NAMES)
# Derived values that may be published (inputs in the public domain).
VALUES = ("s", "v", "g", "g_ex_tga", "g_accel", "d_net", "d_real", "d_usd", "d_ffr", "d_m2")


class Lookup:
    """Last observation on or before a day, like an as-of join. Missing values are skipped, never filled."""

    def __init__(self, rows):
        pairs = sorted((day, float(value)) for day, value in rows if value is not None)
        self.days = [day for day, _ in pairs]
        self.values = [value for _, value in pairs]

    def at(self, day):
        position = bisect_right(self.days, day)
        return self.values[position - 1] if position else None

    def last_day(self):
        return self.days[-1] if self.days else None


def _shift(day, days):
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def _month_end(day):
    first = date.fromisoformat(day).replace(day=1)
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return following - timedelta(days=1)


def weekly_table(series):
    """One row per H.4.1 Wednesday on which every rule can be evaluated.

    ``series`` maps the short names above to [(iso_date, value-or-None)].
    The definitions are those of the study's PROTOCOL.md, in the same order of arithmetic.
    """
    weekly = {k: {day: float(v) for day, v in series[k] if v is not None} for k in WEEKLY}
    daily = {k: Lookup(series[k]) for k in DAILY}
    m2 = Lookup(series["M2"])
    m2_changes = Lookup([(( _month_end(m2.days[i]) + timedelta(days=M2_LAG_DAYS)).isoformat(),
                          m2.values[i] - m2.values[i - 3]) for i in range(3, len(m2.days))])
    calendar = sorted(day for day in weekly["L"] if all(day in weekly[k] for k in WEEKLY))

    base = {}
    for day in calendar:
        earlier = _shift(day, -7 * WINDOW_WEEKS)
        if any(earlier not in weekly[k] for k in WEEKLY):
            continue
        now = {k: weekly[k][day] for k in WEEKLY}
        then = {k: weekly[k][earlier] for k in WEEKLY}
        if then["L"] <= 0:
            continue
        s = (now["L"] - then["L"]) / then["L"] * 100
        g = (now["R"] - then["R"]) / then["L"] * 100
        d_tga = (now["TGA"] - then["TGA"]) / then["L"] * 100
        net_now, net_then = now["L"] - now["TGA"] - now["RRP"], then["L"] - then["TGA"] - then["RRP"]
        base[day] = {"s": s, "g": g, "v": g - s, "g_ex_tga": g + d_tga,
                     "d_net": (net_now - net_then) / then["L"] * 100}

    vix_weekly = {day: daily["VIX"].at(_shift(day, 1)) for day in calendar}
    rows = []
    for position, day in enumerate(calendar):
        if day not in base:
            continue
        thursday = _shift(day, 1)
        before = base.get(_shift(day, -7 * ACCEL_WEEKS))
        window = [vix_weekly[d] for d in calendar[max(0, position - VIX_WEEKS):position]]
        level = {k: daily[k].at(thursday) for k in DAILY}
        lagged = {k: daily[k].at(_shift(thursday, -7 * (FED_WINDOW_WEEKS if k == "FFR" else WINDOW_WEEKS)))
                  for k in ("REAL", "USD", "FFR", "BAA")}
        d_m2 = m2_changes.at(_shift(day, 2))
        if (before is None or len(window) < VIX_WEEKS or any(x is None for x in window)
                or any(x is None for x in level.values()) or any(x is None for x in lagged.values())
                or d_m2 is None):
            continue
        b = base[day]
        values = {**b, "g_accel": b["g"] - before["g"], "d_real": level["REAL"] - lagged["REAL"],
                  "d_usd": (level["USD"] / lagged["USD"] - 1) * 100, "d_ffr": level["FFR"] - lagged["FFR"],
                  "d_m2": d_m2}
        on = {
            "Q1": b["g"] > 0, "Q2": b["v"] > 0, "Q3": b["s"] > 0, "Q4": b["s"] <= 0 and b["v"] > 0,
            "Q5": not (b["s"] > 0 and b["v"] <= 0 and b["g"] <= 0), "Q6": values["g_accel"] > 0,
            "Q7": b["g_ex_tga"] > 0, "Q8": b["d_net"] > 0,
            "F1": level["BAA"] - lagged["BAA"] < 0, "F2": values["d_real"] < 0, "F3": values["d_usd"] < 0,
            "F4": level["VIX"] < median(window), "F5": values["d_ffr"] <= 0, "F6": d_m2 > 0,
        }
        count = sum(on[k] for k in VOTERS)
        on["C1"] = count >= 4
        on["C2"] = count / len(VOTERS)
        on["C3"] = on["Q1"] or on["F1"]
        rows.append({"date": day, "on": on, "count": count, "values": values})
    return rows


def _retained(output, reason, checked_at):
    """Keep the last good board, clearly marked, rather than publish a blank or a guess."""
    if output is not None and output.exists():
        try:
            previous = json.loads(output.read_text())
        except Exception:
            previous = None
        if isinstance(previous, dict) and previous.get("states"):
            return {**previous, "status": "retained", "reason": reason, "checked_at": checked_at}
    return {"schema_version": 1, "status": "unavailable", "reason": reason, "generated_at": checked_at,
            "checked_at": checked_at, "asof": None, "study": STUDY, "names": NAMES, "states": {},
            "score": None, "values": {}, "history": [], "inputs": {}}


def build(history_root, runtime_root, *, clock=None, previous_output=None):
    """The public board. Any missing input yields 'retained' or 'unavailable', never a partial board."""
    root, runtime = Path(history_root), Path(runtime_root)
    now = live_api._utc(clock)
    stamp = live_api._stamp(now)
    index = {}
    if (root / "index.json").exists():
        try:
            index = json.loads((root / "index.json").read_text()).get("series", {})
        except Exception:
            return _retained(previous_output, "history_index_invalid", stamp)
    series = {}
    for names, persist in ((PUBLIC, True), (PRIVATE, False)):
        for key, sid in names.items():
            stored = history.read_series(root, runtime, sid, persist)
            if not stored:
                return _retained(previous_output, "input_missing_" + sid, stamp)
            series[key] = stored
    try:
        rows = weekly_table(series)
    except (ValueError, ZeroDivisionError):
        return _retained(previous_output, "input_invalid", stamp)
    if not rows:
        return _retained(previous_output, "no_complete_week", stamp)
    last = rows[-1]
    if date.fromisoformat(last["date"]) > now.date():
        return _retained(previous_output, "future_observation_rejected", stamp)
    age = (now.date() - date.fromisoformat(last["date"])).days

    def since(key):
        start = last["date"]
        for row in reversed(rows[:-1]):
            if row["on"][key] != last["on"][key]:
                break
            start = row["date"]
        return start

    thursday = _shift(last["date"], 1)
    daily_last = {key: Lookup(series[key]).last_day() for key in DAILY}
    return {
        "schema_version": 1,
        "status": "stale" if age > STALE_AFTER_DAYS else "ok",
        "reason": "latest_week_older_than_%d_days" % STALE_AFTER_DAYS if age > STALE_AFTER_DAYS else None,
        "generated_at": stamp, "checked_at": stamp, "asof": last["date"],
        "decision_date": _shift(last["date"], 2), "study": STUDY, "names": NAMES,
        # Daily inputs are read as of the Thursday after the H.4.1 Wednesday. Until that
        # day's values are in, a rate-based state can still change.
        "daily_complete": all(day is not None and day >= thursday for day in daily_last.values()),
        "score": {"on": last["count"], "of": len(VOTERS), "voters": list(VOTERS)},
        "states": {key: {"on": round(last["on"][key], 4) if key == "C2" else bool(last["on"][key]),
                         "since": since(key)} for key in RULES},
        "values": {key: round(last["values"][key], 3) for key in VALUES},
        "history": [{"d": row["date"], "n": row["count"]} for row in rows[-HISTORY_WEEKS:]],
        "vintage": "current_as_of_retrieval",
        "inputs": {sid: {k: index.get(sid, {}).get(k) for k in ("last_date", "retrieved_at", "status")}
                   for sid in list(PUBLIC.values()) + list(PRIVATE.values())},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--history", type=Path, required=True, help="History store root, e.g. data/history")
    parser.add_argument("--runtime", type=Path, required=True,
                        help="Private runtime store holding the copyrighted inputs, e.g. .private/history")
    parser.add_argument("--output", type=Path, required=True, help="Public board, e.g. data/public/models.json")
    args = parser.parse_args(argv)
    result = build(args.history, args.runtime, previous_output=args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    live_api._atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "reason": result.get("reason"), "asof": result.get("asof"),
                      "score": result.get("score")}, ensure_ascii=False))
    return 0 if result["status"] in ("ok", "stale") else 2


if __name__ == "__main__":
    raise SystemExit(main())
