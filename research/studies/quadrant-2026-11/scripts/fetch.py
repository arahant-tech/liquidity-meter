import json, subprocess, sys, time
from pathlib import Path
D = Path(sys.argv[1]) / "data"
FRED = ["WALCL", "WRBWFRBL", "WLRRAOL", "WLRRAFOIAL", "WDTGAL", "WCURCIR", "TLAACBW027NBOG", "VIXCLS"]
YAHOO = ["SPY", "QQQ", "IWM", "RSP", "XLY", "XLI", "XLF", "XLB", "XLP", "XLU", "XLV", "XLK", "XLE"]
for sid in FRED:
    out = D / f"{sid}.csv"
    subprocess.run(["curl", "-sS", "--fail", "--max-time", "40", "-o", str(out),
                    f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2002-12-01"], check=True)
    rows = out.read_text().strip().split("\n"); print(f"FRED  {sid:15s} {len(rows)-1:5d} rows  last {rows[-1]}")
for sym in YAHOO:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=1038700800&period2=1794787200&interval=1d&events=div%2Csplits"
    body = subprocess.run(["curl", "-sS", "--fail", "--max-time", "60", "-A", "Mozilla/5.0", url], capture_output=True, text=True, check=True).stdout
    res = json.loads(body)["chart"]["result"][0]
    ts, adj = res["timestamp"], res["indicators"]["adjclose"][0]["adjclose"]
    lines = ["date,adjclose"]
    for t, a in zip(ts, adj):
        if a is not None:
            lines.append(time.strftime("%Y-%m-%d", time.gmtime(t + 5 * 3600)) + f",{a:.6f}")
    (D / f"{sym}.csv").write_text("\n".join(lines) + "\n")
    print(f"YAHOO {sym:5s} {len(lines)-1:5d} rows  {lines[1].split(',')[0]} → {lines[-1].split(',')[0]}")
    time.sleep(0.4)
