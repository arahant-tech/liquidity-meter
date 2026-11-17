import sys
from pathlib import Path
import numpy as np, pandas as pd
pd.set_option("display.width", 200)
D = Path(sys.argv[1]) / "data"
H = 13
def fred(sid):
    return pd.to_numeric(pd.read_csv(D / f"{sid}.csv", index_col=0, parse_dates=True).iloc[:, 0], errors="coerce")
def px(sym):
    return pd.read_csv(D / f"{sym}.csv", index_col=0, parse_dates=True).iloc[:, 0].dropna()

L = fred("WALCL").dropna() / 1e3; wed = L.index
R = (fred("WRBWFRBL") / 1e3).reindex(wed); RRP = (fred("WLRRAOL") / 1e3).reindex(wed); FRRP = (fred("WLRRAFOIAL") / 1e3).reindex(wed)
TGA = (fred("WDTGAL") / 1e3).reindex(wed); CUR = (fred("WCURCIR") / 1e3).reindex(wed)
N = L - R; base = L.shift(H)
d = lambda x: x - x.shift(H)
P = pd.DataFrame({"L": L, "R": R, "RRP": RRP, "FRRP": FRRP, "TGA": TGA, "CUR": CUR, "N": N})
P["s"] = 100 * d(L) / base; P["v"] = -100 * d(N) / base; P["g"] = 100 * d(R) / base
P["v_rrp"] = -100 * d(RRP) / base; P["v_tga"] = -100 * d(TGA) / base; P["v_frrp"] = -100 * d(FRRP) / base; P["v_cur"] = -100 * d(CUR) / base
P["v_oth"] = P.v - P.v_rrp - P.v_tga - P.v_frrp - P.v_cur
bank = fred("TLAACBW027NBOG").dropna()
P["adeq"] = 100 * P.R / bank.reindex(wed, method="ffill").shift(1)
P["buf"] = 100 * (P.RRP + P.TGA) / P.L

def windows(series):
    days = series.index
    rows = {}
    for t in wed:
        i0 = days.searchsorted(t - pd.Timedelta(weeks=H), side="right") - 1; i1 = days.searchsorted(t, side="right") - 1
        same = 100 * np.log(series.iloc[i1] / series.iloc[i0]) if i0 >= 0 else np.nan
        ie = days.searchsorted(t + pd.Timedelta(days=2), side="left")
        f4 = f13 = mae = np.nan
        if ie < len(days):
            for span, name in ((28, "f4"), (91, "f13")):
                ix = days.searchsorted(days[ie] + pd.Timedelta(days=span), side="left")
                if ix < len(days):
                    val = 100 * np.log(series.iloc[ix] / series.iloc[ie])
                    if name == "f4": f4 = val
                    else:
                        f13 = val; mae = 100 * (series.iloc[ie:ix + 1].min() / series.iloc[ie] - 1)
        rows[t] = (same, f4, f13, mae)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["same13", "fwd4", "fwd13", "mae13"])

W = {sym: windows(px(sym)) for sym in ("SPY", "QQQ", "IWM", "RSP", "XLY", "XLI", "XLF", "XLB", "XLP", "XLU", "XLV")}
REL = {"QQQ−SPY": W["QQQ"] - W["SPY"], "IWM−SPY": W["IWM"] - W["SPY"], "RSP−SPY": W["RSP"] - W["SPY"],
       "경기민감−방어": (W["XLY"] + W["XLI"] + W["XLF"] + W["XLB"]) / 4 - (W["XLP"] + W["XLU"] + W["XLV"]) / 3}

def ols(y, X):
    Xc = np.column_stack([np.ones(len(y)), X]); beta, *_ = np.linalg.lstsq(Xc, y, rcond=None)
    resid = y - Xc @ beta; r2 = 1 - resid.var() / y.var()
    return beta[1:], r2

def fit(y, cols, mask, stats):
    """stats: dict name -> function(beta) ; circular-shift p for each (upper tail)."""
    ok = mask & y.notna() & P[cols].notna().all(axis=1)
    yy, X = y[ok].to_numpy(), P.loc[ok, cols].to_numpy()
    beta, r2 = ols(yy, X)
    obs = {k: f(beta) for k, f in stats.items()}
    null = {k: [] for k in stats}
    for k in range(1, len(yy)):
        b, _ = ols(yy, np.roll(X, k, axis=0))
        for name, f in stats.items():
            null[name].append(f(b))
    p = {k: float((np.array(null[k]) >= obs[k]).mean()) for k in stats}
    return {"n": int(ok.sum()), "beta": dict(zip(cols, beta)), "r2": r2, "obs": obs, "p": p}

def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i]); adj = [0.0] * len(ps); run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - rank) * ps[i])); adj[i] = run
    return adj

MAIN = (wed >= "2009-01-01"); mser = pd.Series(MAIN, index=wed)
valid = P[["s", "v", "g"]].notna().all(axis=1)
print("0. 항등식과 기초 통계 (2009-01~)")
print(f"   |g − (s+v)| 최대 = {(P.g - P.s - P.v).abs()[valid].max():.2e}  → 분해가 정확히 맞는다")
sub = P[mser & valid]
print(f"   s: 평균 {sub.s.mean():+.2f} 표준편차 {sub.s.std():.2f} | v: 평균 {sub.v.mean():+.2f} 표준편차 {sub.v.std():.2f} | g: 평균 {sub.g.mean():+.2f} 표준편차 {sub.g.std():.2f}  (단위: 총자산 대비 %, 13주)")
print(f"   상관 s·v = {sub.s.corr(sub.v):+.2f}   s·g = {sub.s.corr(sub.g):+.2f}   v·g = {sub.v.corr(sub.g):+.2f}")
print(f"   s<0 & v>0 인 주 {int(((sub.s<0)&(sub.v>0)).sum())} ({100*((sub.s<0)&(sub.v>0)).mean():.0f}%), 그중 준비금이 실제로 는 주(g>0) {int(((sub.s<0)&(sub.v>0)&(sub.g>0)).sum())}")

print("\n1. 1차 질문 (SPY, 2009-01~, 순환이동 p 단측, Holm 6)")
fam = []
for col, lab in (("fwd13", "이후 13주"), ("same13", "같은 13주")):
    y = W["SPY"][col]
    a = fit(y, ["g"], mser, {"b_g": lambda b: b[0]})
    b = fit(y, ["s", "v"], mser, {"b_v−b_s": lambda b: b[1] - b[0], "b_v": lambda b: b[1], "b_s": lambda b: b[0]})
    fam += [(lab, "Q1 준비금↑→주가↑ (b_g>0)", a["beta"]["g"], a["p"]["b_g"], a),
            (lab, "Q2 방향이 총량보다 낫다 (b_v>b_s)", b["obs"]["b_v−b_s"], b["p"]["b_v−b_s"], b),
            (lab, "Q3 방향 자체 (b_v>0)", b["beta"]["v"], b["p"]["b_v"], b)]
adj = holm([f[3] for f in fam])
for (lab, q, est, p, r), a in zip(fam, adj):
    extra = f"b_s {r['beta']['s']:+.2f} b_v {r['beta']['v']:+.2f} (b_s p {r['p']['b_s']:.2f})" if "s" in r["beta"] else f"R² {100*r['r2']:.1f}%"
    print(f"   {lab:8s} {q:32s} 추정 {est:+6.2f}  p {p:.3f}  Holm {a:.3f}   | n {r['n']}  {extra}")
print("   (계수 단위: 총자산 대비 1%p 변화당 수익률 %p)")

print("\n2. 기간별 계수 (SPY) — 부호가 유지되는가")
print(f"   {'기간':17s} {'n':>4s} {'상관s·v':>7s} | 이후13주: {'b_g':>6s} {'b_s':>6s} {'b_v':>6s} | 같은13주: {'b_g':>6s} {'b_s':>6s} {'b_v':>6s}")
for lab, a, b in (("2009-01~2013-12", "2009-01-01", "2013-12-31"), ("2014-01~2019-12", "2014-01-01", "2019-12-31"),
                  ("2020-01~2021-12", "2020-01-01", "2021-12-31"), ("2022-01~2026-11", "2022-01-01", "2026-12-31"), ("2009-01~ 전체", "2009-01-01", "2026-12-31")):
    m = pd.Series((wed >= a) & (wed <= b), index=wed); cells = []
    for col in ("fwd13", "same13"):
        ok = m & W["SPY"][col].notna() & valid
        bg, _ = ols(W["SPY"][col][ok].to_numpy(), P.loc[ok, ["g"]].to_numpy()); bsv, _ = ols(W["SPY"][col][ok].to_numpy(), P.loc[ok, ["s", "v"]].to_numpy())
        cells.append(f"{bg[0]:+6.2f} {bsv[0]:+6.2f} {bsv[1]:+6.2f}")
    print(f"   {lab:17s} {int((m&valid).sum()):4d} {P.s[m&valid].corr(P.v[m&valid]):+7.2f} |           {cells[0]} |           {cells[1]}")

print("\n3. 벡터의 출처별 계수 (SPY, 2009-01~) — r = a + b·s + b·v_rrp + b·v_tga + b·v_나머지")
P["v_rest"] = P.v - P.v_rrp - P.v_tga
for col, lab in (("fwd13", "이후 13주"), ("same13", "같은 13주")):
    r = fit(W["SPY"][col], ["s", "v_rrp", "v_tga", "v_rest"], mser, {c: (lambda b, i=i: b[i]) for i, c in enumerate(["s", "v_rrp", "v_tga", "v_rest"])})
    print(f"   {lab:8s} " + "  ".join(f"{c} {r['beta'][c]:+.2f} (p {r['p'][c]:.2f})" for c in ["s", "v_rrp", "v_tga", "v_rest"]))
print(f"   표준편차: v_rrp {sub.v_rrp.std():.2f}  v_tga {sub.v_tga.std():.2f}  나머지 {(sub.v-sub.v_rrp-sub.v_tga).std():.2f}")

print("\n4. 다른 결과 변수 (2009-01~) — b_s, b_v 와 순환이동 p(단측, 보정 없음)")
print(f"   {'결과':14s} | 이후13주: {'b_s':>6s} {'(p)':>6s} {'b_v':>6s} {'(p)':>6s} | 같은13주: {'b_s':>6s} {'(p)':>6s} {'b_v':>6s} {'(p)':>6s}")
for name, frame in [("QQQ", W["QQQ"])] + list(REL.items()):
    cells = []
    for col in ("fwd13", "same13"):
        r = fit(frame[col], ["s", "v"], mser, {"s": lambda b: b[0], "v": lambda b: b[1]})
        cells.append(f"{r['beta']['s']:+6.2f} {r['p']['s']:6.2f} {r['beta']['v']:+6.2f} {r['p']['v']:6.2f}")
    print(f"   {name:14s} |           {cells[0]} |           {cells[1]}")

print("\n5. 사분면 (SPY, 2009-01~, 평균 로그수익률 %)")
print(f"   {'':22s} {'주':>4s} {'비중':>5s} | {'같은13주':>8s} {'이후4주':>8s} {'이후13주':>8s} {'이후13주 음수':>11s} {'평균 최대역행':>11s}")
def row(lab, f):
    f = f & mser & valid
    y = W["SPY"]
    print(f"   {lab:22s} {int(f.sum()):4d} {100*f.sum()/(mser&valid).sum():4.0f}% | {y.same13[f].mean():+8.2f} {y.fwd4[f].mean():+8.2f} {y.fwd13[f].mean():+8.2f} {100*(y.fwd13[f]<0).mean():10.0f}% {y.mae13[f].mean():+11.2f}")
row("총량↑ 방향↑", (P.s > 0) & (P.v > 0)); row("총량↑ 방향↓", (P.s > 0) & (P.v <= 0)); row("총량↓ 방향↑", (P.s <= 0) & (P.v > 0)); row("총량↓ 방향↓", (P.s <= 0) & (P.v <= 0))
row("  총량↓방향↑ · 준비금↑", (P.s <= 0) & (P.v > 0) & (P.g > 0)); row("  총량↓방향↑ · 준비금↓", (P.s <= 0) & (P.v > 0) & (P.g <= 0))
row("준비금↑ (g>0)", P.g > 0); row("준비금↓ (g≤0)", P.g <= 0); row("전체", valid)

print("\n6. 준비금 충분성(준비금 ÷ 은행 총자산)별 — SPY 이후 13주에 대한 b_s, b_v 와 사분면")
for lab, f in (("충분성 12% 이상", P.adeq >= 12), ("충분성 12% 미만", P.adeq < 12)):
    m = pd.Series(f, index=wed) & mser & valid & W["SPY"].fwd13.notna()
    b, r2 = ols(W["SPY"].fwd13[m].to_numpy(), P.loc[m, ["s", "v"]].to_numpy()); bg, _ = ols(W["SPY"].fwd13[m].to_numpy(), P.loc[m, ["g"]].to_numpy())
    y = W["SPY"]
    print(f"   {lab:14s} n {int(m.sum()):4d} | b_g {bg[0]:+.2f}  b_s {b[0]:+.2f}  b_v {b[1]:+.2f} | 준비금↑일 때 이후13주 {y.fwd13[m&(P.g>0)].mean():+.2f} (음수 {100*(y.fwd13[m&(P.g>0)]<0).mean():.0f}%)  준비금↓일 때 {y.fwd13[m&(P.g<=0)].mean():+.2f} (음수 {100*(y.fwd13[m&(P.g<=0)]<0).mean():.0f}%)")

print("\n7. 지금")
t = P[valid].index[-1]; z = P.loc[t]
print(f"   기준 {t.date()} | 총자산 {z.L:,.0f}B  준비금 {z.R:,.0f}B  국내 역레포 {z.RRP:.1f}B  해외 역레포 {z.FRRP:.0f}B  TGA {z.TGA:.0f}B  현금통화 {z.CUR:,.0f}B")
print(f"   13주: s {z.s:+.2f}  v {z.v:+.2f}  → g {z.g:+.2f}   (v 출처: 역레포 {z.v_rrp:+.2f}  TGA {z.v_tga:+.2f}  해외 {z.v_frrp:+.2f}  현금통화 {z.v_cur:+.2f}  기타 {z.v_oth:+.2f})")
print(f"   준비금 충분성 {z.adeq:.2f}%  | 완충(국내 역레포+TGA) 총자산의 {z.buf:.1f}%")
print("   최근 8개 13주 창(13주 간격은 아니고 매 4주):")
for tt in P[valid].index[-29::4]:
    q = P.loc[tt]; print(f"     {tt.date()}  s {q.s:+5.2f}  v {q.v:+5.2f}  g {q.g:+5.2f}  충분성 {q.adeq:5.2f}%")
