"""Does the reserve-bucket tilt survive two other explanations? Implements PROTOCOL.md exactly.

The sample, the on/off call and the outcome are built by the tilt study's own code, so they cannot drift.
Usage: python study.py <study scripts directory> <output json>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tilt-2026-11" / "scripts"))
import study as tilt  # noqa: E402  (reads sys.argv[1] for the data directory)

OUT = Path(sys.argv[2])
rng = np.random.default_rng(tilt.SEED + 7)
W, DAY, WEEK = tilt.W, tilt.DAY, tilt.WEEK
EXCLUDE = ("2020-03-01", "2021-12-31")


def build():
    L0 = tilt.H["L"].shift(tilt.WINDOW)
    g = (tilt.H["R"] - tilt.H["R"].shift(tilt.WINDOW)) / L0 * 100
    frame = pd.DataFrame({"g": g})[(W >= tilt.START) & g.notna()]
    frame["bg"], _ = tilt.buckets(frame["g"].to_numpy())
    fq, fs = tilt.forward("QQQ", 13), tilt.forward("SPY", 13)
    rq, rs = tilt.forward("QQQ", 1).shift(1), tilt.forward("SPY", 1).shift(1)
    beta = rq.rolling(104).cov(rs) / rs.rolling(104).var()
    frame["y"] = (fq - fs).reindex(frame.index)
    frame["yb"] = (fq - beta * fs).reindex(frame.index)

    spy = tilt.price("SPY")
    close = pd.Series(spy.asof(tilt.FRI).to_numpy(), index=W)                 # Friday close of each decision week
    frame["P13"] = (np.log(close / close.shift(13)) * 100).reindex(frame.index)
    below_high = (close / close.rolling(52).max() - 1) * 100
    frame["DD26"] = below_high.rolling(26).min().reindex(frame.index)
    vix = pd.Series(tilt.fred("VIXCLS").asof(W + DAY).to_numpy(), index=W)
    frame["V"] = (vix / vix.shift(1).rolling(52).median() - 1).reindex(frame.index)
    real = tilt.fred("DFII10")
    frame["DR"] = (pd.Series(real.asof(W + DAY).to_numpy(), index=W)
                   - pd.Series(real.asof(W + DAY - 13 * WEEK).to_numpy(), index=W)).reindex(frame.index)
    sample = frame.dropna()
    sample = sample.assign(ON=(sample["bg"] >= 4).astype(float))
    return sample


def residual(target, controls):
    X = np.column_stack([np.ones(len(target))] + controls)
    return target - X @ np.linalg.lstsq(X, target, rcond=None)[0]


def partial(y, on, controls):
    """Coefficient on ON after the controls, with the larger of a shift p and a block-bootstrap p."""
    ey, eo = (residual(y, controls), residual(on, controls)) if controls else (y - y.mean(), on - on.mean())
    slope = lambda a: float((a * ey).mean() / (a ** 2).mean())
    b = slope(eo)
    shifts = [slope(np.roll(eo, k)) for k in range(26, len(y) - 25)]
    p_shift = (1 + sum(s >= b for s in shifts)) / (len(shifts) + 1)
    count = 0
    for _ in range(tilt.DRAWS):
        draw = eo[tilt.block_indices(rng, len(eo), len(eo))]
        count += slope(draw - draw.mean()) >= b
    p_boot = (1 + count) / (tilt.DRAWS + 1)
    return b, p_shift, p_boot


def coefficients(y, columns):
    X = np.column_stack([np.ones(len(y))] + [c for _, c in columns])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    return {name: tilt.rnd(b, 3) for (name, _), b in zip(columns, beta[1:])}


def stratum(mask, y, on):
    a, b = y[mask & (on == 1)], y[mask & (on == 0)]
    used = len(a) >= 26 and len(b) >= 2
    return {"weeks": int(mask.sum()), "on_weeks": int(len(a)),
            "D": tilt.rnd(a.mean() - b.mean()) if len(a) >= 2 and len(b) >= 2 else None, "used_for_verdict": bool(used)}


def verdict(b, b0, p_holm, strata):
    retention = b / b0
    used = [s for s in strata if s["used_for_verdict"]]
    if b <= 0 or retention < 0.25:
        return "설명된다"
    if p_holm < 0.10 and retention >= 0.5 and all(s["D"] is not None and s["D"] > 0 for s in used):
        return "남는다"
    return "일부 설명된다"


def analyse(sample, outcome="y"):
    y, on = sample[outcome].to_numpy(), sample["ON"].to_numpy()
    c = {k: sample[k].to_numpy() for k in ("P13", "DD26", "V", "DR")}
    tests = {"T_A": ["P13", "DD26", "V"], "T_B": ["DR"], "T_AB": ["P13", "DD26", "V", "DR"]}
    b0 = partial(y, on, [])[0]
    out = {"b0": tilt.rnd(b0, 3), "tests": {}}
    raw = {}
    for name, keys in tests.items():
        b, p_shift, p_boot = partial(y, on, [c[k] for k in keys])
        raw[name] = max(p_shift, p_boot)
        out["tests"][name] = {"controls": keys, "b": tilt.rnd(b, 3), "retention": tilt.rnd(b / b0, 3),
                              "p_shift": tilt.rnd(p_shift, 4), "p_bootstrap": tilt.rnd(p_boot, 4), "p_raw": tilt.rnd(raw[name], 4),
                              "control_coefficients": coefficients(y, [("ON", on)] + [(k, c[k]) for k in keys])}
    running = 0.0
    for rank, name in enumerate(sorted(raw, key=raw.get)):                     # Holm, three tests
        running = max(running, min(1.0, (len(raw) - rank) * raw[name]))
        out["tests"][name]["p_holm"] = tilt.rnd(running, 4)
    return out, y, on, c


def main():
    sample = build()
    idx = sample.index
    main_out, y, on, c = analyse(sample)
    thirds = np.quantile(c["P13"], [1 / 3, 2 / 3])
    strata = {
        "A": {"직전 13주 수익 낮음": stratum(c["P13"] <= thirds[0], y, on),
              "직전 13주 수익 중간": stratum((c["P13"] > thirds[0]) & (c["P13"] <= thirds[1]), y, on),
              "직전 13주 수익 높음": stratum(c["P13"] > thirds[1], y, on),
              "26주 안에 10% 넘게 빠진 적 있음": stratum(c["DD26"] <= -10, y, on),
              "26주 안에 10% 넘게 빠진 적 없음": stratum(c["DD26"] > -10, y, on)},
        "B": {"실질금리 하락": stratum(c["DR"] < 0, y, on), "실질금리 상승·보합": stratum(c["DR"] >= 0, y, on)},
    }
    t = main_out["tests"]
    b0 = main_out["b0"]
    verdicts = {
        "A 폭락 뒤 반등": verdict(t["T_A"]["b"], b0, t["T_A"]["p_holm"], list(strata["A"].values())),
        "B 실질금리": verdict(t["T_B"]["b"], b0, t["T_B"]["p_holm"], list(strata["B"].values())),
        "종합": verdict(t["T_AB"]["b"], b0, t["T_AB"]["p_holm"], list(strata["A"].values()) + list(strata["B"].values())),
    }

    described = {}
    for name, mask in (("on", on == 1), ("off", on == 0)):
        described[name] = {"weeks": int(mask.sum()), "P13_mean": tilt.rnd(c["P13"][mask].mean()),
                           "share_after_10pct_drawdown": tilt.rnd((c["DD26"][mask] <= -10).mean() * 100, 1),
                           "V_mean": tilt.rnd(c["V"][mask].mean(), 3), "DR_mean": tilt.rnd(c["DR"][mask].mean(), 3),
                           "share_real_yield_falling": tilt.rnd((c["DR"][mask] < 0).mean() * 100, 1)}
    correlations = {k: tilt.rnd(float(np.corrcoef(on, c[k])[0, 1]), 3) for k in c}

    beta_out = analyse(sample, "yb")[0]
    keep = ~((idx >= EXCLUDE[0]) & (idx <= EXCLUDE[1]))
    trimmed = sample[keep]
    trimmed_out = analyse(trimmed)[0]

    falling = (c["DR"] < 0).astype(float)
    reverse = {"D_real_yield_falling": tilt.rnd(tilt.gap(falling, y)),
               "after_ON": dict(zip(("b", "p_shift", "p_bootstrap"), (tilt.rnd(v, 4) for v in partial(y, falling, [on]))))}

    results = {
        "schema_version": 1, "study": "tilt-controls-2026-11", "asof": str(idx[-1].date()),
        "sample": {"from": str(idx[0].date()), "to": str(idx[-1].date()), "weeks": len(idx)},
        "primary": main_out, "strata": strata, "verdicts": verdicts,
        "on_versus_off": described, "correlation_with_ON": correlations,
        "beta_adjusted": beta_out,
        "without_2020_03_to_2021_12": {"weeks": int(keep.sum()), "on_weeks": int(trimmed["ON"].sum()),
                                       "D": tilt.rnd(tilt.gap(trimmed["ON"].to_numpy(), trimmed["y"].to_numpy())), **trimmed_out},
        "reverse": reverse,
    }
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1, allow_nan=False) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
