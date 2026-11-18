"""Growth-tilt experiment: reserve-change bucket → QQQ over SPY. Implements PROTOCOL.md exactly.

Usage: python study.py <study scripts directory> <output json>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(sys.argv[1]) / "data"
OUT = Path(sys.argv[2])
WEEK, DAY = pd.Timedelta(weeks=1), pd.Timedelta(days=1)
START, MIN_HISTORY, WINDOW, HORIZON = "2009-01-01", 156, 13, 13
PERIODS = ["2017-01-01", "2022-01-01"]
REVIEWS = (104, 156, 208, 260)
MIN_GROUP = 26
DRAWS, BLOCK, SEED = 5000, 26, 20261118


def fred(sid):
    frame = pd.read_csv(D / f"{sid}.csv", parse_dates=[0], index_col=0, na_values=["."])
    return pd.to_numeric(frame.iloc[:, 0], errors="coerce").dropna()


def price(sym):
    return pd.read_csv(D / f"{sym}.csv", parse_dates=["date"], index_col="date")["close"]


H = pd.DataFrame({"L": fred("WALCL"), "R": fred("WRBWFRBL"), "TGA": fred("WDTGAL"), "RRP": fred("WLRRAOL")}).dropna()
W = H.index
FRI = W + 2 * DAY


def buckets(values):
    """Quintile against strictly earlier values only (same rule as lm/measure/quadrant.py)."""
    out, bounds, past = [], [], []
    for value in values:
        if len(past) >= MIN_HISTORY:
            edges = np.quantile(past, [.2, .4, .6, .8])
            out.append(1 + int((edges < value).sum()))
            bounds.append(edges)
        else:
            out.append(np.nan)
            bounds.append(None)
        past.append(value)
    return np.array(out), bounds


def forward(sym, weeks):
    p = price(sym)
    p0 = pd.Series(p.asof(FRI).to_numpy(), index=W)
    p1 = pd.Series(p.asof(FRI + weeks * WEEK).to_numpy(), index=W)
    ok = (FRI + weeks * WEEK <= p.index.max()) & (FRI >= p.index.min())
    return (np.log(p1 / p0) * 100).where(ok)


def gap(on, y):
    """D: mean outcome in tilt-on weeks minus mean in tilt-off weeks (percentage points)."""
    keep = np.isfinite(on) & np.isfinite(y)
    a, b = y[keep & (on == 1)], y[keep & (on == 0)]
    return None if len(a) < 2 or len(b) < 2 else float(a.mean() - b.mean())


def shift_p(on, y):
    obs = gap(on, y)
    stats = [gap(np.roll(on, k), y) for k in range(26, len(y) - 25)]
    return (1 + sum(s is not None and s >= obs for s in stats)) / (len(stats) + 1), len(stats)


def block_indices(rng, n, length):
    """Stationary bootstrap indices: blocks of geometric length with mean BLOCK, wrapping around."""
    jump = rng.random(length) < 1 / BLOCK
    jump[0] = True
    starts = rng.integers(0, n, size=length)
    index = np.empty(length, dtype=int)
    for t in range(length):
        index[t] = starts[t] if jump[t] else (index[t - 1] + 1) % n
    return index


def bootstrap_p(on, y, rng):
    obs = gap(on, y)
    count = 0
    for _ in range(DRAWS):
        stat = gap(on[block_indices(rng, len(on), len(on))], y)
        count += stat is not None and stat >= obs
    return (1 + count) / (DRAWS + 1)


def rnd(value, digits=2):
    return None if value is None or not np.isfinite(value) else round(float(value), digits)


def performance(pos, r_on, r_off, cash, cost=0.0010):
    prev = np.r_[pos[0], pos[:-1]]
    r = pos * r_on + (1 - pos) * r_off - cost * np.abs(pos - prev)
    equity = np.cumprod(1 + r)
    years = len(r) / 52
    excess = r - cash
    return {"cagr": rnd((equity[-1] ** (1 / years) - 1) * 100, 1), "sharpe": rnd(excess.mean() / excess.std() * np.sqrt(52)),
            "mdd": rnd((equity / np.maximum.accumulate(equity) - 1).min() * 100, 1),
            "qqq_share": round(float(pos.mean()) * 100), "turns_per_year": rnd(np.abs(pos - prev).sum() / years, 1)}


def simulate(on, weekly, rng):
    """Section 6: what D_live looks like after N scored weeks, with and without the historical relation."""
    n, longest = len(on), max(REVIEWS)
    out = {"H0": np.full((DRAWS, len(REVIEWS)), np.nan), "H1": np.full((DRAWS, len(REVIEWS)), np.nan)}
    for name in out:
        for d in range(DRAWS):
            index = block_indices(rng, n, longest + HORIZON - 1)
            state = on[index[:longest]]
            flows = weekly[index] if name == "H1" else weekly[block_indices(rng, n, longest + HORIZON - 1)]
            y = np.convolve(flows, np.ones(HORIZON), mode="valid")           # forward 13-week sums
            for j, review in enumerate(REVIEWS):
                s, o = state[:review], y[:review]
                if (s == 1).sum() >= MIN_GROUP and (s == 0).sum() >= MIN_GROUP:
                    out[name][d, j] = o[s == 1].mean() - o[s == 0].mean()
    return out


def main():
    rng = np.random.default_rng(SEED)
    L0 = H["L"].shift(WINDOW)
    s = (H["L"] - L0) / L0 * 100
    g = (H["R"] - H["R"].shift(WINDOW)) / L0 * 100
    x = g + (H["TGA"] - H["TGA"].shift(WINDOW)) / L0 * 100
    v = g - s
    rrp_share = H["RRP"] / H["L"] * 100
    live = (W >= START) & g.notna()
    frame = pd.DataFrame({"s": s, "v": v, "g": g, "x": x, "rrp_share": rrp_share, "rrp": H["RRP"] / 1e3})[live]
    frame["bg"], bounds_g = buckets(frame["g"].to_numpy())
    frame["bx"], bounds_x = buckets(frame["x"].to_numpy())

    fq = {h: forward("QQQ", h) for h in (1, 4, 13, 26)}
    fs = {h: forward("SPY", h) for h in (1, 4, 13, 26)}
    for h in (1, 4, 13, 26):
        frame[f"y{h}"] = (fq[h] - fs[h]).reindex(frame.index)
    frame["spy13"] = fs[13].reindex(frame.index)
    # Beta from the 104 weekly returns already realised at each decision Friday.
    rq, rs = fq[1].shift(1), fs[1].shift(1)
    beta = rq.rolling(104).cov(rs) / rs.rolling(104).var()
    frame["beta"] = beta.reindex(frame.index)
    frame["yb13"] = (fq[13] - beta * fs[13]).reindex(frame.index)

    sample = frame.dropna(subset=["bg", "bx", "y13", "yb13"])
    idx = sample.index
    y = sample["y13"].to_numpy()
    cuts = [idx[0]] + [pd.Timestamp(d) for d in PERIODS] + [idx[-1] + DAY]
    results = {"schema_version": 1, "study": "tilt-2026-11",
               "sample": {"from": str(idx[0].date()), "to": str(idx[-1].date()), "weeks": len(idx),
                          "independent_13w": round(len(idx) / 13, 1)},
               "mean_y13": rnd(y.mean()), "sd_y13": rnd(y.std()), "indicators": {}}

    raw = {}
    for key, column, label in (("g", "bg", "준비금 13주 변화"), ("x", "bx", "TGA를 뺀 준비금 변화")):
        on = (sample[column] >= 4).astype(float).to_numpy()
        p_shift, shifts = shift_p(on, y)
        p_boot = bootstrap_p(on, y, rng)
        raw[key] = max(p_shift, p_boot)
        per = [gap(on[(idx >= a) & (idx < b)], y[(idx >= a) & (idx < b)]) for a, b in zip(cuts, cuts[1:])]
        delayed = gap(np.r_[np.nan, on[:-1]], y)
        r_on = np.expm1(sample["y1"].to_numpy() / 100 + fs[1].reindex(idx).to_numpy() / 100)
        r_off = np.expm1(fs[1].reindex(idx).to_numpy() / 100)
        cash = (pd.Series(fred("DTB3").asof(W + DAY).to_numpy(), index=W) / 100 / 52).reindex(idx).to_numpy()
        results["indicators"][key] = {
            "name": label, "on_weeks": int(on.sum()), "off_weeks": int((1 - on).sum()),
            "mean_on": rnd(y[on == 1].mean()), "mean_off": rnd(y[on == 0].mean()), "D": rnd(gap(on, y)),
            "p_shift": rnd(p_shift, 4), "p_bootstrap": rnd(p_boot, 4), "p_raw": rnd(raw[key], 4), "shifts": shifts,
            "periods": [rnd(p) for p in per], "delayed": rnd(delayed),
            "beta_adjusted_D": rnd(gap(on, sample["yb13"].to_numpy())),
            "spy_direction_D": rnd(gap(on, sample["spy13"].to_numpy())),
            "by_bucket": {str(b): {"weeks": int((sample[column] == b).sum()),
                                   "mean": rnd(y[(sample[column] == b).to_numpy()].mean()),
                                   "beta_adjusted": rnd(sample["yb13"].to_numpy()[(sample[column] == b).to_numpy()].mean()),
                                   "spy": rnd(sample["spy13"].to_numpy()[(sample[column] == b).to_numpy()].mean())}
                          for b in (1, 2, 3, 4, 5)},
            "horizons": {str(h): rnd(gap(on, sample[f"y{h}"].to_numpy())) for h in (4, 26)},
            "thresholds": {"5": rnd(gap((sample[column] >= 5).astype(float).to_numpy(), y)),
                           "3+": rnd(gap((sample[column] >= 3).astype(float).to_numpy(), y))},
            "weekly_spread_per_year": rnd(np.cov(on, sample["y1"].to_numpy())[0, 1] / on.var() * 52, 1),
            "strategy": performance(on, r_on, r_off, cash),
            "switches": int(np.abs(np.diff(on)).sum()),
        }
        if key == "g":
            results["benchmarks"] = {"SPY만": performance(np.zeros(len(on)), r_on, r_off, cash, 0.0),
                                     "QQQ만": performance(np.ones(len(on)), r_on, r_off, cash, 0.0),
                                     "절반씩": performance(np.full(len(on), 0.5), r_on, r_off, cash, 0.0)}
    order = sorted(raw, key=raw.get)
    holm = {order[0]: min(1.0, 2 * raw[order[0]])}
    holm[order[1]] = max(holm[order[0]], min(1.0, raw[order[1]]))
    for key, item in results["indicators"].items():
        item["p_holm"] = rnd(holm[key], 4)
        positive = [p is not None and p > 0 for p in item["periods"]]
        if (holm[key] < 0.10 and all(positive) and item["delayed"] > 0 and item["beta_adjusted_D"] > 0):
            item["grade"] = "통과"
        elif raw[key] < 0.10 and sum(positive) >= 2:
            item["grade"] = "후보"
        else:
            item["grade"] = "미달"

    def cell(mask):
        mask = np.asarray(mask)
        return {"weeks": int(mask.sum()), "mean": rnd(y[mask].mean()) if mask.sum() else None,
                "beta_adjusted": rnd(sample["yb13"].to_numpy()[mask].mean()) if mask.sum() else None}

    on_g, on_x = (sample["bg"] >= 4).to_numpy(), (sample["bx"] >= 4).to_numpy()
    sv, vv, gv = sample["s"].to_numpy(), sample["v"].to_numpy(), sample["g"].to_numpy()
    results["source"] = {
        "on_and_total_up": cell(on_g & (sv > 0)), "on_and_total_not_up": cell(on_g & (sv <= 0)),
        "total_down_direction_up_reserves_up": cell((sv <= 0) & (vv > 0) & (gv > 0)),
        "total_down_direction_up_reserves_down": cell((sv <= 0) & (vv > 0) & (gv <= 0)),
    }
    results["tga_discount"] = {"both_on": cell(on_g & on_x), "g_on_x_off": cell(on_g & ~on_x),
                               "g_off_x_on": cell(~on_g & on_x), "both_off": cell(~on_g & ~on_x),
                               "agreement_share": rnd((on_g == on_x).mean() * 100, 1)}

    # Watch items: when total assets started growing by more than 1%p, and when the reverse repo facility refilled.
    def episodes(flag):
        flag = flag.to_numpy()
        starts = np.flatnonzero(flag & ~np.r_[False, flag[:-1]])
        rows = []
        for i in starts:
            run = 1
            while i + run < len(flag) and flag[i + run]:
                run += 1
            later = frame["bg"].to_numpy()[i:i + 27]
            later = later[np.isfinite(later)]
            rows.append({"start": str(frame.index[i].date()), "weeks": int(run),
                         "bucket_at_start": None if not np.isfinite(frame["bg"].to_numpy()[i]) else int(frame["bg"].to_numpy()[i]),
                         "max_bucket_next_26w": None if not len(later) else int(later.max()),
                         "weeks_on_next_26w": None if not len(later) else int((later >= 4).sum())})
        return rows

    results["watch"] = {"total_assets_above_1pp": episodes(frame["s"] > 1.0),
                        "rrp_share_above_1pct": episodes(frame["rrp_share"] >= 1.0),
                        "share_of_on_weeks_with_total_above_1pp": rnd((sample["s"].to_numpy()[on_g] > 1.0).mean() * 100, 1)}

    # Section 6: thresholds for the live record, fixed by the pre-declared method.
    sims = simulate(on_g.astype(float), sample["y1"].to_numpy(), rng)
    pass_line = float(np.nanpercentile(sims["H0"][:, -1], 90))
    kill_lines = [float(np.nanpercentile(sims["H1"][:, j], 5)) for j in range(len(REVIEWS))]
    live = {"reviews": list(REVIEWS), "min_group_weeks": MIN_GROUP, "pass_line_at_260": rnd(pass_line),
            "kill_lines": [rnd(k) for k in kill_lines],
            "median_D": {name: [rnd(np.nanmedian(sims[name][:, j])) for j in range(len(REVIEWS))] for name in sims},
            "review_possible_share": {name: [rnd(np.isfinite(sims[name][:, j]).mean() * 100, 1) for j in range(len(REVIEWS))]
                                      for name in sims}, "outcomes": {}}
    for name, values in sims.items():
        killed_at, alive = [], np.ones(DRAWS, bool)
        for j in range(len(REVIEWS)):
            hit = alive & np.isfinite(values[:, j]) & (values[:, j] <= kill_lines[j])
            killed_at.append(rnd(hit.mean() * 100, 1))
            alive &= ~hit
        passed = alive & np.isfinite(values[:, -1]) & (values[:, -1] >= pass_line)
        live["outcomes"][name] = {"killed_at_each_review": killed_at, "killed_total": rnd((~alive).mean() * 100, 1),
                                  "passed": rnd(passed.mean() * 100, 1),
                                  "unresolved": rnd((alive & ~passed).mean() * 100, 1)}
    results["live_criteria"] = live

    last = frame.iloc[-1]
    results["now"] = {"week": str(frame.index[-1].date()), "g": rnd(last["g"], 3), "g_bucket": int(last["bg"]),
                      "g_bounds": [rnd(b, 3) for b in bounds_g[-1]], "x": rnd(last["x"], 3), "x_bucket": int(last["bx"]),
                      "x_bounds": [rnd(b, 3) for b in bounds_x[-1]], "tilt_g": bool(last["bg"] >= 4),
                      "tilt_x": bool(last["bx"] >= 4), "s": rnd(last["s"], 3), "v": rnd(last["v"], 3),
                      "rrp_billion": rnd(last["rrp"], 1), "rrp_share": rnd(last["rrp_share"], 3)}
    results["asof"] = results["now"]["week"]
    if "--parity" in sys.argv:          # for checking the buckets against the published map by hand
        results["bucket_series_for_parity"] = {str(d.date()): (None if not np.isfinite(b) else int(b))
                                               for d, b in zip(frame.index, frame["bg"])}
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
