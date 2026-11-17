import sys, io, json, contextlib, runpy
from pathlib import Path
import numpy as np, pandas as pd
STUDY, OUT = sys.argv[1], Path(sys.argv[2])
sys.argv = [STUDY + "/study.py", STUDY]
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(STUDY + "/study.py")
P, W, REL, wed, valid, ols, fit, windows, px, holm = (g[k] for k in ("P", "W", "REL", "wed", "valid", "ols", "fit", "windows", "px", "holm"))
W["BTC"] = windows(px("BTC-USD")); W["ETH"] = windows(px("ETH-USD"))
ASSETS = {"SPY": W["SPY"], "QQQ": W["QQQ"], "QQQ−SPY": REL["QQQ−SPY"], "BTC": W["BTC"], "ETH": W["ETH"]}
S = lambda a, b="2026-12-31": pd.Series((wed >= a) & (wed <= b), index=wed)
EQ, BT, ET = S("2009-01-01"), S("2014-10-01"), S("2017-11-15")

cell = pd.Series(index=wed, dtype=object)
c = np.select([(P.s > 0) & (P.v > 0), (P.s > 0) & (P.v <= 0) & (P.g > 0), (P.s > 0) & (P.v <= 0), (P.s <= 0) & (P.v > 0) & (P.g > 0), (P.s <= 0) & (P.v > 0)],
              ["A", "B+", "B-", "C+", "C-"], default="D")
cell[valid] = c[valid.to_numpy()]
CELLS = ["A", "B+", "B-", "C+", "C-", "D"]
NAMES = {"A": "총량↑ 방향↑", "B+": "총량↑ 방향↓ · 준비금↑", "B-": "총량↑ 방향↓ · 준비금↓", "C+": "총량↓ 방향↑ · 준비금↑", "C-": "총량↓ 방향↑ · 준비금↓", "D": "총량↓ 방향↓"}

def runs(flag):
    f = flag.to_numpy().astype(bool); out = []; i = 0
    while i < len(f):
        if f[i]:
            j = i
            while j + 1 < len(f) and f[j + 1]: j += 1
            out.append((flag.index[i], flag.index[j], j - i + 1)); i = j + 1
        else: i += 1
    return out

def shift_p(y, flag):
    ok = y.notna() & flag.notna(); yy, f = y[ok].to_numpy(), flag[ok].to_numpy().astype(bool)
    if f.sum() < 8 or (~f).sum() < 8: return None
    obs = yy[f].mean() - yy[~f].mean()
    null = np.array([yy[np.roll(f, k)].mean() - yy[~np.roll(f, k)].mean() for k in range(1, len(yy))])
    return float(obs), float((null >= obs).mean()), float((null <= obs).mean())

res = {"asof": str(P[valid].index[-1].date())}
print("1. 여섯 칸의 구성")
for name, m in (("2009-01~", EQ), ("2014-10~ (BTC 표본)", BT)):
    print(f"  표본 {name}")
    rows = []
    for k in CELLS:
        f = (cell == k) & m; rs = runs(((cell == k) & m)[m])
        rows.append({"cell": k, "weeks": int(f.sum()), "share": float(f.sum() / (m & valid).sum()), "episodes": len(rs), "long": sum(1 for r in rs if r[2] >= 4),
                     "s": float(P.s[f].mean()), "v": float(P.v[f].mean()), "g": float(P.g[f].mean())})
        print(f"    {k:2s} {NAMES[k]:22s} {rows[-1]['weeks']:4d}주 {100*rows[-1]['share']:4.0f}%  구간 {rows[-1]['episodes']:3d}(4주+ {rows[-1]['long']:2d})  평균 s {rows[-1]['s']:+6.2f} v {rows[-1]['v']:+6.2f} g {rows[-1]['g']:+6.2f}")
    res.setdefault("cells", {})[name] = rows

print("\n2. 칸별 이후 13주 평균 로그수익률 % (괄호: 양(+) 비율) — BTC 표본 2014-10~ / ETH 는 2017-11~")
res["cell_returns"] = {}
head = "    칸   주  | " + " | ".join(f"{a:^15s}" for a in ASSETS)
print(head)
for k in CELLS + ["g>0", "g<=0", "all"]:
    flag = (P.g > 0) if k == "g>0" else (P.g <= 0) if k == "g<=0" else pd.Series(True, index=wed) if k == "all" else (cell == k)
    cells, rec = [], {}
    for a, frame in ASSETS.items():
        m = (ET if a == "ETH" else BT) & valid & frame.fwd13.notna(); f = flag & m
        if f.sum() >= 8:
            sp = shift_p(frame.fwd13[m], flag[m]) if k not in ("all",) else None
            rec[a] = {"n": int(f.sum()), "fwd13": float(frame.fwd13[f].mean()), "hit": float((frame.fwd13[f] > 0).mean()), "median": float(frame.fwd13[f].median()),
                      "same13": float(frame.same13[f & frame.same13.notna()].mean()), "fwd4": float(frame.fwd4[f].mean()),
                      "p_hi": sp[1] if sp else None, "p_lo": sp[2] if sp else None, "mae": float(frame.mae13[f].mean()) if "mae13" in frame else None}
            cells.append(f"{rec[a]['fwd13']:+7.1f} ({100*rec[a]['hit']:3.0f}%)")
        else:
            rec[a] = None; cells.append(f"{'—':^15s}")
    res["cell_returns"][k] = rec
    nB = int((flag & BT & valid).sum())
    print(f"    {k:4s} {nB:4d} | " + " | ".join(f"{x:^15s}" for x in cells))

print("\n   같은 13주(동행) 평균 %")
for k in CELLS:
    print(f"    {k:4s}      | " + " | ".join(f"{(res['cell_returns'][k][a]['same13'] if res['cell_returns'][k][a] else float('nan')):^+15.1f}" for a in ASSETS))

print("\n3. 1차 질문 — BTC (2014-10~), 순환이동 p 단측, Holm 6")
fam = []
for col, lab in (("fwd13", "이후 13주"), ("same13", "같은 13주")):
    a = fit(W["BTC"][col], ["g"], BT, {"b_g": lambda b: b[0]})
    b = fit(W["BTC"][col], ["s", "v"], BT, {"d": lambda b: b[1] - b[0], "v": lambda b: b[1], "s": lambda b: b[0]})
    fam += [(lab, "Q1 b_g>0", a["beta"]["g"], a["p"]["b_g"], a["n"], a["r2"]), (lab, "Q2 b_v>b_s", b["obs"]["d"], b["p"]["d"], b["n"], b["r2"]), (lab, "Q3 b_v>0", b["beta"]["v"], b["p"]["v"], b["n"], b["r2"])]
    res.setdefault("btc_primary_coefs", {})[col] = {"b_g": a["beta"]["g"], "b_s": b["beta"]["s"], "b_v": b["beta"]["v"], "p_s": b["p"]["s"], "r2_g": a["r2"]}
adj = holm([f[3] for f in fam]); res["btc_primary"] = []
for (lab, q, est, p, n, r2), h in zip(fam, adj):
    print(f"    {lab:8s} {q:10s} 추정 {est:+6.2f}  p {p:.3f}  Holm {h:.3f}  n {n}  R² {100*r2:.1f}%")
    res["btc_primary"].append({"window": lab, "q": q, "est": float(est), "p": float(p), "holm": float(h), "n": n})
print("    계수:", {k: {kk: round(float(vv), 3) for kk, vv in v.items()} for k, v in res["btc_primary_coefs"].items()})

print("\n4. 자산별 계수 (이후 13주) — b_g 와 b_s, b_v, 기간별 b_g")
res["coefs"] = {}
PERIODS = (("2014-10~2019-12", "2014-10-01", "2019-12-31"), ("2020-01~2021-12", "2020-01-01", "2021-12-31"), ("2022-01~2026-11", "2022-01-01", "2026-12-31"))
for a, frame in ASSETS.items():
    m = ET if a == "ETH" else BT
    r1 = fit(frame.fwd13, ["g"], m, {"g": lambda b: b[0]}); r2 = fit(frame.fwd13, ["s", "v"], m, {"s": lambda b: b[0], "v": lambda b: b[1]})
    per = []
    for lab, x, y in PERIODS:
        mm = S(x, y) & m & valid & frame.fwd13.notna()
        per.append(float(ols(frame.fwd13[mm].to_numpy(), P.loc[mm, ["g"]].to_numpy())[0][0]) if mm.sum() > 40 else None)
    res["coefs"][a] = {"n": r1["n"], "b_g": float(r1["beta"]["g"]), "p_g": r1["p"]["g"], "r2": float(r1["r2"]), "b_s": float(r2["beta"]["s"]), "p_s": r2["p"]["s"],
                       "b_v": float(r2["beta"]["v"]), "p_v": r2["p"]["v"], "periods": per}
    print(f"    {a:8s} n {r1['n']:3d} | b_g {r1['beta']['g']:+6.2f} (p {r1['p']['g']:.3f}, R² {100*r1['r2']:4.1f}%) | b_s {r2['beta']['s']:+6.2f} (p {r2['p']['s']:.2f})  b_v {r2['beta']['v']:+6.2f} (p {r2['p']['v']:.2f}) | 기간별 b_g " + " ".join("   —  " if x is None else f"{x:+6.2f}" for x in per))

print("\n5. 과거값만으로 나눈 준비금 변화(g) 다섯 구간 — 이후 13주 평균 % (양 비율), 2014-10~ (구간 경계는 2009년부터의 과거값)")
gs = P.g[valid & EQ]; bucket = pd.Series(index=gs.index, dtype=float)
for i in range(156, len(gs)):
    bucket.iloc[i] = 1 + np.searchsorted(np.quantile(gs.iloc[:i].to_numpy(), [.2, .4, .6, .8]), gs.iloc[i], side="left")
bucket = bucket.reindex(wed); res["buckets"] = {}
for k in (1, 2, 3, 4, 5):
    rec = {}; cells = []
    for a, frame in ASSETS.items():
        m = (ET if a == "ETH" else BT) & valid & frame.fwd13.notna(); f = (bucket == k) & m
        rec[a] = {"n": int(f.sum()), "fwd13": float(frame.fwd13[f].mean()) if f.sum() else None, "hit": float((frame.fwd13[f] > 0).mean()) if f.sum() else None,
                  "mae": float(frame.mae13[f].mean()) if f.sum() and "mae13" in frame else None}
        cells.append(f"{rec[a]['fwd13']:+7.1f} ({100*rec[a]['hit']:3.0f}%) n{rec[a]['n']:3d}" if f.sum() else "—")
    res["buckets"][k] = rec; print(f"    {k}구간 | " + " | ".join(cells))

print("\n6. 위험 — BTC·SPY 이후 13주: 평균 최대역행 %, −20%(BTC)/−10%(SPY) 이하로 끝난 비율")
res["risk"] = {}
for k in CELLS + ["g>0", "g<=0"]:
    flag = (P.g > 0) if k == "g>0" else (P.g <= 0) if k == "g<=0" else (cell == k)
    f = flag & BT & valid & W["BTC"].fwd13.notna()
    res["risk"][k] = {"n": int(f.sum()), "btc_mae": float(W["BTC"].mae13[f].mean()), "btc_bad": float((W["BTC"].fwd13[f] < -20).mean()), "spy_mae": float(W["SPY"].mae13[f].mean()), "spy_bad": float((W["SPY"].fwd13[f] < -10).mean())}
    r = res["risk"][k]; print(f"    {k:4s} n {r['n']:3d} | BTC 최대역행 {r['btc_mae']:+6.1f}  −20% 이하 {100*r['btc_bad']:3.0f}% | SPY 최대역행 {r['spy_mae']:+5.1f}  −10% 이하 {100*r['spy_bad']:3.0f}%")

print("\n7. C+ (총량↓ 방향↑ 준비금↑) 구간 목록, 4주 이상, 2014-10~")
res["cplus"] = []
for a0, b0, n in runs(((cell == "C+") & BT)[BT]):
    if n >= 4:
        def ret(sym):
            s_ = px(sym); return float(100 * np.log(s_[:b0].iloc[-1] / s_[:a0].iloc[-1]))
        rec = {"start": str(a0.date()), "end": str(b0.date()), "weeks": n, "SPY": ret("SPY"), "QQQ": ret("QQQ"), "BTC": ret("BTC-USD")}
        res["cplus"].append(rec); print(f"    {rec['start']} ~ {rec['end']} {n:3d}주 | SPY {rec['SPY']:+6.1f}  QQQ {rec['QQQ']:+6.1f}  BTC {rec['BTC']:+7.1f}")

t = P[valid].index[-1]; z = P.loc[t]
res["now"] = {"date": str(t.date()), "s": float(z.s), "v": float(z.v), "g": float(z.g), "cell": cell[t], "bucket": int(bucket[t]), "L": float(z.L), "R": float(z.R), "RRP": float(z.RRP),
              "TGA": float(z.TGA), "adeq": float(z.adeq), "bounds": [float(x) for x in np.quantile(gs.iloc[:-1], [.2, .4, .6, .8])]}
print("\n8. 지금:", res["now"])
res["points"] = [{"d": str(i.date()), "s": round(float(r.s), 3), "v": round(float(r.v), 3), "c": cell[i]} for i, r in P[valid & EQ].iterrows()]
res["names"] = NAMES
OUT.write_text(json.dumps(res, ensure_ascii=False))
print(f"\nJSON → {OUT} ({OUT.stat().st_size/1e3:.0f} KB), 점 {len(res['points'])}개")
