import sys, io, json, contextlib, runpy
from pathlib import Path
import numpy as np, pandas as pd
STUDY, OUT = sys.argv[1], Path(sys.argv[2])
sys.argv = [__file__.replace("stability.py", "quadrant.py"), STUDY, "/dev/null"]
with contextlib.redirect_stdout(io.StringIO()):
    q = runpy.run_path(sys.argv[0])
P, W, ASSETS, wed, valid, ols, cell, S, BT, ET, runs, px, CELLS = (q[k] for k in ("P", "W", "ASSETS", "wed", "valid", "ols", "cell", "S", "BT", "ET", "runs", "px", "CELLS"))
res = json.loads(OUT.read_text())
PER = (("2014-10~2019-12", "2014-10-01", "2019-12-31"), ("2020-01~2021-12", "2020-01-01", "2021-12-31"), ("2022-01~2026-11", "2022-01-01", "2026-12-31"))

print("A. BTC·ETH: 총량(b_s)과 방향(b_v) 계수의 기간별 값 (총자산 1%p 당 13주 수익률 %p)")
res["stability"] = {"coef_periods": {}, "g_sign_periods": {}, "years": {}, "bminus": [], "cell_periods": {}}
for a in ("BTC", "ETH", "SPY", "QQQ"):
    rows = []
    for lab, x, y in PER:
        cells = []
        for col in ("fwd13", "same13"):
            m = S(x, y) & (ET if a == "ETH" else BT) & valid & ASSETS[a][col].notna()
            if m.sum() > 40:
                b, _ = ols(ASSETS[a][col][m].to_numpy(), P.loc[m, ["s", "v"]].to_numpy()); cells.append((float(b[0]), float(b[1])))
            else: cells.append(None)
        rows.append(cells)
    res["stability"]["coef_periods"][a] = rows
    print(f"   {a:4s} 이후13주 " + "  ".join(f"[{lab[:7]}] b_s {c[0][0]:+6.2f} b_v {c[0][1]:+6.2f}" if c[0] else f"[{lab[:7]}]   —" for (lab, _, _), c in zip(PER, rows)))
    print(f"        같은13주 " + "  ".join(f"[{lab[:7]}] b_s {c[1][0]:+6.2f} b_v {c[1][1]:+6.2f}" if c[1] else f"[{lab[:7]}]   —" for (lab, _, _), c in zip(PER, rows)))

print("\nB. 준비금↑(g>0) − 준비금↓(g≤0): 이후 13주 평균 수익률 차이 %p, 기간별")
for a in ASSETS:
    row = []
    for lab, x, y in PER:
        m = S(x, y) & (ET if a == "ETH" else BT) & valid & ASSETS[a].fwd13.notna()
        up, dn = ASSETS[a].fwd13[m & (P.g > 0)], ASSETS[a].fwd13[m & (P.g <= 0)]
        row.append(float(up.mean() - dn.mean()) if len(up) >= 8 and len(dn) >= 8 else None)
    res["stability"]["g_sign_periods"][a] = row
    print(f"   {a:8s} " + "  ".join("   —  " if r is None else f"{r:+6.1f}" for r in row))

print("\nC. 연도별 같은 차이 (양(+)인 해 수 / 비교 가능한 해 수)")
for a in ASSETS:
    pos = tot = 0; cells = []
    for year in range(2015, 2027):
        m = pd.Series(wed.year == year, index=wed) & valid & ASSETS[a].fwd13.notna()
        up, dn = ASSETS[a].fwd13[m & (P.g > 0)], ASSETS[a].fwd13[m & (P.g <= 0)]
        if len(up) >= 4 and len(dn) >= 4:
            d = up.mean() - dn.mean(); tot += 1; pos += d > 0; cells.append(f"{year % 100}:{d:+.0f}")
    res["stability"]["years"][a] = [int(pos), int(tot)]
    print(f"   {a:8s} {pos}/{tot}   " + " ".join(cells))

print("\nD. B−(총량↑ 방향↓ 준비금↓) 구간, 4주 이상, 2014-10~ — 구간 뒤 13주 수익률(구간 마지막 주 기준 이후 13주)")
for a0, b0, n in runs(((cell == "B-") & BT)[BT]):
    if n >= 4:
        rec = {"start": str(a0.date()), "end": str(b0.date()), "weeks": n}
        for sym, key in (("SPY", "SPY"), ("QQQ", "QQQ"), ("BTC-USD", "BTC")):
            s_ = px(sym); rec[key] = float(100 * np.log(s_[:b0].iloc[-1] / s_[:a0].iloc[-1]))
        rec["tga"] = float(P.TGA[b0] - P.TGA[a0]); rec["rrp"] = float(P.RRP[b0] - P.RRP[a0]); rec["res"] = float(P.R[b0] - P.R[a0]); rec["walcl"] = float(P.L[b0] - P.L[a0])
        res["stability"]["bminus"].append(rec)
        print(f"   {rec['start']} ~ {rec['end']} {n:3d}주 | 구간 중 SPY {rec['SPY']:+6.1f} QQQ {rec['QQQ']:+6.1f} BTC {rec['BTC']:+7.1f} | 총자산 {rec['walcl']:+6.0f}B 준비금 {rec['res']:+6.0f}B 역레포 {rec['rrp']:+6.0f}B TGA {rec['tga']:+6.0f}B")

print("\nE. 칸별 BTC 이후 13주 평균 — 기간별 (주 수)")
for k in CELLS:
    row = []
    for lab, x, y in PER:
        m = S(x, y) & BT & valid & W["BTC"].fwd13.notna() & (cell == k)
        row.append((int(m.sum()), float(W["BTC"].fwd13[m].mean()) if m.sum() >= 8 else None, float(W["SPY"].fwd13[m].mean()) if m.sum() >= 8 else None))
    res["stability"]["cell_periods"][k] = row
    print(f"   {k:3s} " + "  ".join(f"{n:3d}주 BTC {('  —  ' if b is None else f'{b:+6.1f}')} SPY {('  — ' if s is None else f'{s:+5.1f}')}" for n, b, s in row))

print("\nF. 칸 대 나머지 — 순환이동 p (이후 13주, 위쪽/아래쪽)")
for k in ("B-", "C+", "A", "D"):
    print(f"   {k:3s} " + "  ".join(f"{a} 위 {res['cell_returns'][k][a]['p_hi']:.2f}/아래 {res['cell_returns'][k][a]['p_lo']:.2f}" for a in ("SPY", "QQQ", "BTC", "ETH") if res['cell_returns'][k][a]))
print("\nG. 최근 30주 칸:", " ".join(cell[valid].iloc[-30:].tolist()))
res["recent_cells"] = [{"d": str(i.date()), "c": c} for i, c in cell[valid].iloc[-30:].items()]
OUT.write_text(json.dumps(res, ensure_ascii=False))
