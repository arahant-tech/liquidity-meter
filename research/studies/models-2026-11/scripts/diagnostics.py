"""Post-hoc checks on the composite score (C2). Written after seeing the results; not used for grades.

Usage: python diagnostics.py <study scripts directory> <output json> <results.json from study.py>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RESULTS = json.loads(Path(sys.argv[3]).read_text())
import study as st  # noqa: E402  (reads sys.argv[1] for the data directory)

P, X, cell, values = st.build(13)
out = {"note": "결과를 본 뒤에 한 점검이다. 등급에는 쓰지 않았다."}


def newey_west_t(pos, x, lags=13):
    p, y = pos - pos.mean(), x - x.mean()
    beta = (p * y).mean() / (p ** 2).mean()
    u = p * (y - beta * p)
    n = len(u)
    s = (u ** 2).sum()
    for lag in range(1, lags + 1):
        s += 2 * (1 - lag / (lags + 1)) * (u[lag:] * u[:-lag]).sum()
    return float(beta / (np.sqrt(s) / (p ** 2).sum()))


for name, spec in st.ASSETS.items():
    r_on, r_off, cash, close = st.returns(name)
    x_all = np.log1p(r_on) - np.log1p(r_off)
    keep = (st.W >= spec["start"]) & x_all.notna() & P.notna().all(axis=1) & X.notna().all(axis=1)
    idx, x = st.W[keep], x_all[keep].to_numpy()
    votes = P.loc[idx, st.VOTERS].to_numpy()
    count = votes.sum(axis=1)
    score = votes.mean(axis=1)
    row = {"weeks": len(x), "newey_west_t": round(newey_west_t(score, x), 2)}

    # Mean annualised excess log return (%) by number of conditions that are on.
    groups = {"0~2": count <= 2, "3": count == 3, "4": count == 4, "5~7": count >= 5}
    row["by_count"] = {k: {"weeks": int(m.sum()), "mean": round(float(x[m].mean() * 5200), 1),
                           "negative_share": round(float((x[m] < 0).mean() * 100))}
                       for k, m in groups.items()}

    # Year by year: weeks with 4+ on against weeks with 3 or fewer.
    years = pd.Series(idx.year, index=idx)
    table, wins, both = {}, 0, 0
    for year in sorted(years.unique()):
        m = (years == year).to_numpy()
        hi, lo = m & (count >= 4), m & (count <= 3)
        if hi.sum() >= 8 and lo.sum() >= 8:
            a, b = x[hi].mean() * 5200, x[lo].mean() * 5200
            table[str(year)] = [int(hi.sum()), round(float(a)), int(lo.sum()), round(float(b))]
            both += 1
            wins += a > b
    row["years"] = {"table_hi_weeks_mean_lo_weeks_mean": table, "hi_better": int(wins), "comparable": both}

    # Without the most extreme weeks, and without each condition in turn.
    order = np.argsort(-np.abs(x))
    trimmed = np.ones(len(x), bool)
    trimmed[order[: max(1, len(x) // 50)]] = False
    row["spread_all"] = round(st.spread(score, x), 1)
    row["spread_without_top_2pct_weeks"] = round(st.spread(score[trimmed], x[trimmed]), 1)
    leave = {}
    for j, key in enumerate(st.VOTERS):
        rest = np.delete(votes, j, axis=1).mean(axis=1)
        _, raw, _, _ = st.shift_test(rest[:, None], x)
        leave[key] = {"spread": round(st.spread(rest, x), 1), "p_raw": round(float(raw[0]), 4)}
    row["leave_one_out"] = leave

    # Liquidity-only and conditions-only halves of the score.
    for label, keys in (("유동성 3개(Q1·Q2·Q7)", ["Q1", "Q2", "Q7"]), ("조건 4개(F1~F4)", ["F1", "F2", "F3", "F4"])):
        part = P.loc[idx, keys].to_numpy().mean(axis=1)
        _, raw, _, _ = st.shift_test(part[:, None], x)
        row[label] = {"spread": round(st.spread(part, x), 1), "p_raw": round(float(raw[0]), 4)}
    out[name] = row



def bootstrap_family(positions, x, draws=5000, mean_block=26, seed=20261117):
    """Second opinion on the shift test: stationary block bootstrap of all rules' states together."""
    rng = np.random.default_rng(seed)
    n, m = positions.shape
    xc = x - x.mean()

    def slopes(block):
        pc = block - block.mean(axis=0)
        var = (pc ** 2).mean(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(var > 1e-12, (pc * xc[:, None]).mean(axis=0) / var * 5200, np.nan)

    obs = slopes(positions)
    stats = np.empty((draws, m))
    for d in range(draws):
        jump = rng.random(n) < 1 / mean_block
        jump[0] = True
        starts = rng.integers(0, n, size=n)
        index = np.empty(n, dtype=int)
        for t in range(n):
            index[t] = starts[t] if jump[t] else (index[t - 1] + 1) % n
        stats[d] = slopes(positions[index])
    mu, sd = np.nanmean(stats, axis=0), np.nanstd(stats, axis=0)
    z, z_obs = (stats - mu) / sd, (obs - mu) / sd
    z_max = np.nanmax(z, axis=1)
    raw = (1 + (stats >= obs).sum(axis=0)) / (draws + 1)
    family = (1 + (z_max[:, None] >= z_obs[None, :]).sum(axis=0)) / (draws + 1)
    return z_obs, raw, family


for name, spec in st.ASSETS.items():
    r_on, r_off, cash, close = st.returns(name)
    x_all = np.log1p(r_on) - np.log1p(r_off)
    keep = (st.W >= spec["start"]) & x_all.notna() & P.notna().all(axis=1) & X.notna().all(axis=1)
    z_obs, raw, family = bootstrap_family(P.loc[st.W[keep]].to_numpy(), x_all[keep].to_numpy())
    out[name]["block_bootstrap"] = {
        key: {"z": round(float(z_obs[j]), 2), "p_raw": round(float(raw[j]), 4),
              "p_family": round(float(family[j]), 4), "p_global": round(min(1.0, float(family[j]) * 5), 4)}
        for j, key in enumerate(st.FIXED)}

# Conservative reading: a pass must hold under both tests. Otherwise it is shown as a candidate.
for name in st.ASSETS:
    grades = {}
    for key, row in RESULTS["assets"][name]["models"].items():
        second = out[name]["block_bootstrap"].get(key)
        if row["grade"] == "통과" and (second is None or second["p_global"] >= 0.10):
            grades[key] = "후보"
        else:
            grades[key] = row["grade"]
    out[name]["conservative_grades"] = grades

corr = P[st.VOTERS].dropna().loc["2010-01-01":].corr().round(2)
out["voter_correlation"] = {a: {b: float(corr.loc[a, b]) for b in st.VOTERS} for a in st.VOTERS}
Path(sys.argv[2]).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
print("wrote", sys.argv[2])
