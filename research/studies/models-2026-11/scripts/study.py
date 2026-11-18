"""Provisional model zoo: quadrant rules, FRED condition rules and combinations.

Implements PROTOCOL.md exactly. Reads scripts/data/ (see fetch.py), writes results.json.
Usage: python study.py <study scripts directory> <output json>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(sys.argv[1]) / "data"
OUT = Path(sys.argv[2])
WEEK = pd.Timedelta(weeks=1)
DAY = pd.Timedelta(days=1)
BURN = 156                      # weeks before a walk-forward model is evaluated
VOTERS = ["Q1", "Q2", "Q7", "F1", "F2", "F3", "F4"]
FIXED = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8",
         "F1", "F2", "F3", "F4", "F5", "F6", "C1", "C2", "C3"]
FORWARD = ["Q9", "C4", "C5"]
NAMES = {
    "Q1": "준비금 증가", "Q2": "방향 양", "Q3": "총량 양", "Q4": "총량↓ 방향↑",
    "Q5": "총량↑·준비금↓ 회피", "Q6": "준비금 가속", "Q7": "재무부 현금 제외",
    "Q8": "통설 순유동성", "Q9": "전진식 칸 규칙",
    "F1": "신용 스프레드 축소", "F2": "실질금리 하락", "F3": "달러 약세",
    "F4": "변동성 낮음", "F5": "연준 비긴축", "F6": "M2 증가",
    "C1": "다수결", "C2": "점수 비례", "C3": "둘 다 나쁠 때만 회피",
    "C4": "전진식 릿지 회귀", "C5": "실시간 선택",
}
ASSETS = {
    "SPY": {"start": "2010-01-01", "cost": 0.0005,
            "periods": ["2015-01-01", "2020-01-01"]},
    "QQQ": {"start": "2010-01-01", "cost": 0.0005,
            "periods": ["2015-01-01", "2020-01-01"]},
    "QQQ↔SPY": {"start": "2010-01-01", "cost": 0.0010,
                "periods": ["2015-01-01", "2020-01-01"]},
    "BTC": {"start": "2014-10-01", "cost": 0.0020,
            "periods": ["2019-01-01", "2023-01-01"]},
    "ETH": {"start": "2017-11-15", "cost": 0.0020,
            "periods": ["2021-01-01", "2024-01-01"]},
}


def fred(sid):
    frame = pd.read_csv(D / f"{sid}.csv", parse_dates=[0], index_col=0, na_values=["."])
    return pd.to_numeric(frame.iloc[:, 0], errors="coerce").dropna()


def price(sym):
    return pd.read_csv(D / f"{sym}.csv", parse_dates=["date"], index_col="date")["close"]


H = pd.DataFrame({"L": fred("WALCL"), "R": fred("WRBWFRBL"),
                  "TGA": fred("WDTGAL"), "RRP": fred("WLRRAOL")}).dropna()
W = H.index                                   # H.4.1 Wednesdays
THU, FRI = W + DAY, W + 2 * DAY
DAILY = {k: fred(k) for k in ("BAA10Y", "DFII10", "DTWEXBGS", "VIXCLS", "DFEDTARU", "DTB3")}
M2 = fred("M2SL")


def level(series, lag_weeks=0):
    return pd.Series(series.asof(THU - lag_weeks * WEEK).to_numpy(), index=W)


def classify(s, v):
    g = s + v
    if s > 0 and v > 0:
        return "A"
    if s > 0:
        return "B+" if g > 0 else "B-"
    if v > 0:
        return "C+" if g > 0 else "C-"
    return "D"


def build(window=13):
    """Weekly states of the 17 fixed rules, the ridge inputs and the cell, by H.4.1 Wednesday."""
    L0 = H["L"].shift(window)
    s = (H["L"] - L0) / L0 * 100
    g = (H["R"] - H["R"].shift(window)) / L0 * 100
    v = g - s
    d_tga = (H["TGA"] - H["TGA"].shift(window)) / L0 * 100
    net = H["L"] - H["TGA"] - H["RRP"]
    d_net = (net - net.shift(window)) / L0 * 100
    d_baa = level(DAILY["BAA10Y"]) - level(DAILY["BAA10Y"], window)
    d_real = level(DAILY["DFII10"]) - level(DAILY["DFII10"], window)
    d_usd = (level(DAILY["DTWEXBGS"]) / level(DAILY["DTWEXBGS"], window) - 1) * 100
    vix = level(DAILY["VIXCLS"])
    vix_median = vix.shift(1).rolling(52).median()
    d_ffr = level(DAILY["DFEDTARU"]) - level(DAILY["DFEDTARU"], 26)
    # Monthly M2: usable 35 days after the end of its month.
    m2_change = (M2 - M2.shift(3)).dropna()
    m2_change.index = m2_change.index + pd.offsets.MonthEnd(0) + 35 * DAY
    d_m2 = pd.Series(m2_change.asof(FRI).to_numpy(), index=W)

    raw = {
        "Q1": g > 0, "Q2": v > 0, "Q3": s > 0, "Q4": (s <= 0) & (v > 0),
        "Q5": ~((s > 0) & (v <= 0) & (g <= 0)), "Q6": (g - g.shift(4)) > 0,
        "Q7": (g + d_tga) > 0, "Q8": d_net > 0,
        "F1": d_baa < 0, "F2": d_real < 0, "F3": d_usd < 0, "F4": vix < vix_median,
        "F5": d_ffr <= 0, "F6": d_m2 > 0,
    }
    needs = {
        "Q1": g, "Q2": v, "Q3": s, "Q4": s, "Q5": s, "Q6": g.shift(4), "Q7": g + d_tga, "Q8": d_net,
        "F1": d_baa, "F2": d_real, "F3": d_usd, "F4": vix_median, "F5": d_ffr, "F6": d_m2,
    }
    P = pd.DataFrame({k: raw[k].astype(float).where(needs[k].notna()) for k in raw})
    votes = P[VOTERS]
    P["C1"] = (votes.sum(axis=1) >= 4).astype(float).where(votes.notna().all(axis=1))
    P["C2"] = votes.mean(axis=1).where(votes.notna().all(axis=1))
    P["C3"] = (~((P["Q1"] == 0) & (P["F1"] == 0))).astype(float).where(P[["Q1", "F1"]].notna().all(axis=1))
    X = pd.DataFrame({"s": s, "v": v, "d_tga": d_tga, "d_baa": d_baa, "d_real": d_real,
                      "d_usd": d_usd, "vix_rel": vix / vix_median - 1})
    cell = pd.Series([classify(a, b) if np.isfinite(a) and np.isfinite(b) else None
                      for a, b in zip(s, v)], index=W)
    values = pd.DataFrame({"s": s, "v": v, "g": g, "g_ex_tga": g + d_tga, "g_accel": g - g.shift(4),
                           "d_net": d_net, "d_baa": d_baa, "d_real": d_real, "d_usd": d_usd,
                           "vix": vix, "vix_median": vix_median, "d_ffr": d_ffr, "d_m2": d_m2})
    return P[FIXED], X, cell, values


def returns(name):
    """Friday-close to next-Friday-close simple returns for the 'on' and 'off' legs, by Wednesday."""
    cash = level(DAILY["DTB3"]) / 100 / 52

    def leg(sym):
        p = price(sym)
        p0 = pd.Series(p.asof(FRI).to_numpy(), index=W)
        p1 = pd.Series(p.asof(FRI + WEEK).to_numpy(), index=W)
        ok = (FRI + WEEK <= p.index.max()) & (FRI >= p.index.min())
        return (p1 / p0 - 1).where(ok), p0.where(FRI >= p.index.min())

    if name == "QQQ↔SPY":
        on, close_on = leg("QQQ")
        off, close_off = leg("SPY")
        return on, off, cash, close_on / close_off
    on, close = leg({"BTC": "BTC-USD", "ETH": "ETH-USD"}.get(name, name))
    return on, cash, cash, close


def spread(pos, x):
    """Annualised timing spread in % per year: cov(pos, x) / var(pos) × 52."""
    keep = np.isfinite(pos) & np.isfinite(x)
    p, y = pos[keep], x[keep]
    if len(p) < 26 or p.var() < 1e-12:
        return None
    return float(((p * y).mean() - p.mean() * y.mean()) / p.var() * 5200)


def shift_test(positions, x):
    """Raw and family-wise one-sided p from circular shifts applied to all models at once."""
    n = len(x)
    xc = (x - x.mean())[:, None]
    pc = positions - positions.mean(axis=0)
    var = (pc ** 2).mean(axis=0)
    live = var > 1e-12
    obs = np.full(positions.shape[1], np.nan)
    obs[live] = (pc[:, live] * xc).mean(axis=0) / var[live] * 5200
    shifts = np.arange(26, n - 25)
    stats = np.full((len(shifts), positions.shape[1]), np.nan)
    for i, k in enumerate(shifts):
        stats[i, live] = (np.roll(pc[:, live], k, axis=0) * xc).mean(axis=0) / var[live] * 5200
    p_raw = (1 + (stats >= obs).sum(axis=0)) / (len(shifts) + 1)
    mu, sd = np.nanmean(stats, axis=0), np.nanstd(stats, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        z, z_obs = (stats - mu) / sd, (obs - mu) / sd
    z_max = np.nanmax(z[:, live], axis=1)
    p_family = (1 + (z_max[:, None] >= z_obs[None, :]).sum(axis=0)) / (len(shifts) + 1)
    return obs, np.where(live, p_raw, np.nan), np.where(live, p_family, np.nan), len(shifts)


def performance(pos, r_on, r_off, cash, cost):
    prev = np.r_[pos[0], pos[:-1]]
    r = pos * r_on + (1 - pos) * r_off - cost * np.abs(pos - prev)
    equity = np.cumprod(1 + r)
    years = len(r) / 52
    excess = r - cash
    return {
        "cagr": round(float(equity[-1] ** (1 / years) - 1) * 100, 1),
        "vol": round(float(r.std() * np.sqrt(52)) * 100, 1),
        "sharpe": round(float(excess.mean() / excess.std() * np.sqrt(52)), 2),
        "mdd": round(float((equity / np.maximum.accumulate(equity) - 1).min()) * 100, 1),
        "exposure": round(float(pos.mean()) * 100),
        "turns_per_year": round(float(np.abs(pos - prev).sum() / years), 1),
    }


def cell_rule(cells, x, first):
    """Q9: hold when the current cell's realised next-week mean so far is at least the overall mean."""
    out = np.full(len(x), np.nan)
    for t in range(first, len(x)):
        past_x, past_c = x[:t], cells[:t]
        same = past_x[past_c == cells[t]]
        out[t] = 1.0 if len(same) < 20 or same.mean() >= past_x.mean() else 0.0
    return out


def ridge_rule(X, x, first, lam=10.0, horizon=4, refit=13):
    """C4: expanding ridge on standardised inputs; target is the next four weeks' excess log return."""
    n = len(x)
    target = np.full(n, np.nan)
    for t in range(n - horizon + 1):
        target[t] = x[t:t + horizon].sum()
    out = np.full(n, np.nan)
    for start in range(first, n, refit):
        train = slice(0, start - horizon + 1)          # targets fully realised before `start`
        A, y = X[train], target[train]
        mu, sd = A.mean(axis=0), A.std(axis=0)
        Z = (A - mu) / sd
        beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
        block = slice(start, min(start + refit, n))
        out[block] = ((((X[block] - mu) / sd) @ beta + y.mean()) > 0).astype(float)
    return out


def pick_rule(positions, x, first):
    """C5: each week follow the fixed rule with the best timing spread so far."""
    out = np.full(len(x), np.nan)
    chosen = []
    for t in range(first, len(x)):
        scores = [spread(positions[:t, j], x[:t]) for j in range(positions.shape[1])]
        scores = [-np.inf if s is None else s for s in scores]
        best = int(np.argmax(scores))
        chosen.append(best)
        out[t] = positions[t, best]
    return out, chosen


def grade(p_global, p_raw, periods, delayed):
    positive = [p is not None and p > 0 for p in periods]
    if p_global is not None and p_global < 0.10 and all(positive) and delayed is not None and delayed > 0:
        return "통과"
    if p_raw is not None and p_raw < 0.05 and sum(positive) >= 2:
        return "후보"
    return "미달"


def rounded(value, digits=1):
    return None if value is None or not np.isfinite(value) else round(float(value), digits)


def main():
    P13, X13, cell13, values = build(13)
    alt = {w: build(w)[0] for w in (8, 26)}
    results = {"schema_version": 1, "study": "models-2026-11", "models": NAMES,
               "fixed": FIXED, "forward": FORWARD, "voters": VOTERS, "assets": {}}

    for name, spec in ASSETS.items():
        r_on, r_off, cash, close = returns(name)
        x_all = np.log1p(r_on) - np.log1p(r_off)
        keep = (W >= spec["start"]) & x_all.notna() & P13.notna().all(axis=1) & X13.notna().all(axis=1)
        idx = W[keep]
        x = x_all[keep].to_numpy()
        fixed = P13.loc[idx].to_numpy()
        n = len(x)
        first = BURN

        forward = np.column_stack([
            cell_rule(cell13.loc[idx].to_numpy(), x, first),
            ridge_rule(X13.loc[idx].to_numpy(), x, first),
            pick_rule(fixed, x, first)[0],
        ])
        _, chosen = pick_rule(fixed, x, first)
        everything = np.column_stack([fixed, forward])
        keys = FIXED + FORWARD

        obs_f, raw_f, fam_f, shifts_f = shift_test(fixed, x)
        obs_w, raw_w, fam_w, shifts_w = shift_test(everything[first:], x[first:])

        bounds = [idx[0]] + [pd.Timestamp(d) for d in spec["periods"]] + [idx[-1] + DAY]
        legs = {"on": r_on[keep].to_numpy(), "off": r_off[keep].to_numpy(), "cash": cash[keep].to_numpy()}

        def describe(j, key):
            pos = everything[:, j]
            is_forward = key in FORWARD
            lo = first if is_forward else 0
            p, y = pos[lo:], x[lo:]
            if is_forward:
                cut = [lo + round((n - lo) * f / 3) for f in range(4)]
                per = [spread(pos[a:b], x[a:b]) for a, b in zip(cut, cut[1:])]
                k = keys.index(key)
                obs, raw, fam = obs_w[k], raw_w[k], fam_w[k]
            else:
                per = [spread(pos[(idx >= a) & (idx < b)], x[(idx >= a) & (idx < b)])
                       for a, b in zip(bounds, bounds[1:])]
                obs, raw, fam = obs_f[j], raw_f[j], fam_f[j]
            delayed = spread(np.r_[np.nan, p[:-1]], y)
            raw = None if not np.isfinite(raw) else float(raw)
            fam = None if not np.isfinite(fam) else float(fam)
            p_global = None if fam is None else min(1.0, fam * len(ASSETS))
            row = {
                "name": NAMES[key], "kind": "forward" if is_forward else "fixed",
                "weeks": int(len(y)), "from": str(idx[lo].date()),
                "spread": rounded(obs), "p_raw": rounded(raw, 4), "p_family": rounded(fam, 4),
                "p_global": rounded(p_global, 4), "periods": [rounded(v) for v in per],
                "delayed": rounded(delayed),
                "perf": performance(p, legs["on"][lo:], legs["off"][lo:], legs["cash"][lo:], spec["cost"]),
                "grade": grade(p_global, raw, per, delayed),
            }
            if not is_forward and key in alt[8].columns and key not in ("F4", "F5", "F6"):
                row["windows"] = {str(w): rounded(spread(alt[w].loc[idx, key].to_numpy(), x)) for w in (8, 26)}
            return row

        models = {key: describe(j, key) for j, key in enumerate(keys)}

        # Baselines, outside the family: always hold, and hold above the asset's own 40-week mean.
        full_close = close.dropna()
        trend = (full_close > full_close.rolling(40).mean()).astype(float).reindex(idx).to_numpy()
        hold = np.ones(n)
        base = {}
        for label, pos, lo in (("보유", hold, 0), ("보유(전진식 구간)", hold, first),
                               ("40주 추세", trend, 0), ("40주 추세(전진식 구간)", trend, first)):
            base[label] = performance(pos[lo:], legs["on"][lo:], legs["off"][lo:], legs["cash"][lo:], spec["cost"])
        _, raw_t, _, _ = shift_test(trend[:, None], x)
        base["40주 추세"]["spread"] = rounded(spread(trend, x))
        base["40주 추세"]["p_raw"] = rounded(float(raw_t[0]), 4)
        if name == "QQQ↔SPY":
            base["SPY만"] = performance(np.zeros(n), legs["on"], legs["off"], legs["cash"], 0.0)

        counts = {FIXED[j]: int(c) for j, c in zip(*np.unique(chosen, return_counts=True))}
        results["assets"][name] = {
            "from": str(idx[0].date()), "to": str(idx[-1].date()), "weeks": n,
            "forward_from": str(idx[first].date()), "forward_weeks": n - first,
            "shifts": {"fixed": int(shifts_f), "forward": int(shifts_w)},
            "cost_bp": round(spec["cost"] * 1e4), "models": models, "baselines": base,
            "pick_history": counts, "pick_now": FIXED[chosen[-1]],
            "mean_excess_per_year": rounded(float(x.mean() * 5200)),
        }

        # State for the decision made on the latest Friday (the week after the last scored one).
        latest = P13.dropna().index[-1]
        if "now" not in results:
            row, vals = P13.loc[latest], values.loc[latest]

            def since(key):
                column = P13[key].dropna()
                changed = column.ne(column.shift()).to_numpy().nonzero()[0]
                return str(column.index[changed[-1]].date())

            results["now"] = {
                "week": str(latest.date()), "cell": cell13.loc[latest],
                # Moody's and Cboe data are copyrighted: their states are published, their values are not.
                "values": {k: rounded(v, 3) for k, v in vals.items()
                           if k not in ("d_baa", "vix", "vix_median")},
                "states": {k: {"on": rounded(row[k], 2), "since": since(k)} for k in FIXED},
                "forward": {},
            }
        # Walk-forward rules need the asset's own history; evaluate them at `latest` too.
        cells_now = np.append(cell13.loc[idx].to_numpy(), cell13.loc[latest])
        x_now = np.r_[x, np.nan]
        q9 = cell_rule(cells_now, x_now, n)[-1]
        X_now = np.vstack([X13.loc[idx].to_numpy(), X13.loc[latest].to_numpy()])
        last_fit = first + ((n - first) // 13) * 13
        c4 = ridge_rule(X_now, x_now, last_fit)[-1] if last_fit <= n else None
        P_now = np.vstack([fixed, P13.loc[latest].to_numpy()])
        c5, picked = pick_rule(P_now, x_now, n)
        results["now"]["forward"][name] = {
            "Q9": rounded(q9, 2), "C4": rounded(c4, 2), "C5": rounded(c5[-1], 2), "C5_follows": FIXED[picked[-1]]}

    results["asof"] = results["now"]["week"]
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
