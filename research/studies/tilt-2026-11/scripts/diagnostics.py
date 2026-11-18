"""Post-hoc checks for the tilt experiment. Written after seeing the results; none of it sets a grade.

Usage: python diagnostics.py <study scripts directory> <output json>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import study as st  # noqa: E402  (reads sys.argv[1] for the data directory)

rng = np.random.default_rng(st.SEED + 1)
L0 = st.H["L"].shift(st.WINDOW)
s = (st.H["L"] - L0) / L0 * 100
g = (st.H["R"] - st.H["R"].shift(st.WINDOW)) / L0 * 100
frame = pd.DataFrame({"s": s, "g": g})[(st.W >= st.START) & g.notna()]
frame["bg"], _ = st.buckets(frame["g"].to_numpy())
fq, fs = st.forward("QQQ", 13), st.forward("SPY", 13)
rq, rs = st.forward("QQQ", 1).shift(1), st.forward("SPY", 1).shift(1)
beta = rq.rolling(104).cov(rs) / rs.rolling(104).var()
frame["y"] = (fq - fs).reindex(frame.index)
frame["yb"] = (fq - beta * fs).reindex(frame.index)
frame["spy"] = fs.reindex(frame.index)
sample = frame.dropna()
idx = sample.index
cuts = [idx[0]] + [pd.Timestamp(d) for d in st.PERIODS] + [idx[-1] + st.DAY]


def describe(on, y):
    on = np.asarray(on, float)
    p_shift, _ = st.shift_p(on, y)
    return {"on_weeks": int(on.sum()), "D": st.rnd(st.gap(on, y)), "p_shift": st.rnd(p_shift, 4),
            "p_bootstrap": st.rnd(st.bootstrap_p(on, y, rng), 4),
            "periods": [st.rnd(st.gap(on[(idx >= a) & (idx < b)], y[(idx >= a) & (idx < b)])) for a, b in zip(cuts, cuts[1:])]}


on = (sample["bg"] >= 4).to_numpy()
y, yb, spy, sv = (sample[k].to_numpy() for k in ("y", "yb", "spy", "s"))
out = {"note": "결과를 본 뒤에 한 점검이다. 등급에는 쓰지 않았다."}

# Index direction: the same on/off against SPY's own next 13 weeks.
out["spy_direction"] = {**describe(on, spy), "mean_on": st.rnd(spy[on].mean()), "mean_off": st.rnd(spy[~on].mean()),
                        "off_weeks_with_positive_return_share": st.rnd((spy[~on] > 0).mean() * 100, 1)}

# Where the tilt-on weeks came from, and whether total assets alone do the same job.
years = pd.Series(idx.year, index=idx)
out["on_weeks_by_year"] = {str(year): {"total_up": int((on & (sv > 0) & (years == year).to_numpy()).sum()),
                                       "total_not_up": int((on & (sv <= 0) & (years == year).to_numpy()).sum())}
                           for year in sorted(years.unique()) if (on & (years == year).to_numpy()).any()}
out["alternatives_post_hoc"] = {
    "총자산 13주 변화 > +1%p": {**describe(sv > 1.0, y), "beta_adjusted_D": st.rnd(st.gap((sv > 1.0).astype(float), yb))},
    "총자산 13주 변화 > 0": {**describe(sv > 0, y), "beta_adjusted_D": st.rnd(st.gap((sv > 0).astype(float), yb))},
    "4~5구간이고 총자산도 증가": {**describe(on & (sv > 0), y), "beta_adjusted_D": st.rnd(st.gap((on & (sv > 0)).astype(float), yb))},
}
# Inside tilt-on weeks only: did it matter whether total assets were growing?
inside = on
a, b = y[inside & (sv > 0)], y[inside & (sv <= 0)]
out["inside_on_weeks"] = {"total_up_mean": st.rnd(a.mean()), "total_not_up_mean": st.rnd(b.mean()),
                          "total_not_up_weeks": int(len(b)), "total_not_up_independent_13w": st.rnd(len(b) / 13, 1)}
Path(sys.argv[2]).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
print(json.dumps(out, ensure_ascii=False, indent=1))
