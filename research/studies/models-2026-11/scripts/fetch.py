"""Download the inputs for this study into scripts/data/ (never committed).

FRED series come from the public graph CSV; prices from Yahoo's chart API.
Usage: python fetch.py <study scripts directory>
"""
import json
import subprocess
import sys
import time
from pathlib import Path

D = Path(sys.argv[1]) / "data"
D.mkdir(parents=True, exist_ok=True)
FRED = ["WALCL", "WRBWFRBL", "WLRRAOL", "WDTGAL", "BAA10Y", "DFII10", "DTWEXBGS",
        "VIXCLS", "DFEDTARU", "M2SL", "DTB3"]
ETF = ["SPY", "QQQ"]
CRYPTO = ["BTC-USD", "ETH-USD"]


def curl(url, *extra):
    return subprocess.run(["curl", "-sS", "--fail", "--max-time", "60", *extra, url],
                          capture_output=True, text=True, check=True).stdout


for sid in FRED:
    text = curl(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2002-12-01")
    (D / f"{sid}.csv").write_text(text)
    rows = text.strip().split("\n")
    print(f"FRED  {sid:10s} {len(rows) - 1:5d} rows  {rows[1].split(',')[0]} → {rows[-1]}")
    time.sleep(0.5)

for sym in ETF + CRYPTO:
    crypto = sym in CRYPTO
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=1038700800&period2=1794787200&interval=1d&events=div%2Csplits")
    res = json.loads(curl(url, "-A", "Mozilla/5.0"))["chart"]["result"][0]
    values = (res["indicators"]["quote"][0]["close"] if crypto
              else res["indicators"]["adjclose"][0]["adjclose"])
    lines, seen = ["date,close"], set()
    for stamp, value in zip(res["timestamp"], values):
        # ETF bars are stamped at the New York open; crypto bars at 00:00 UTC.
        day = time.strftime("%Y-%m-%d", time.gmtime(stamp if crypto else stamp + 5 * 3600))
        if value is not None and day not in seen:
            seen.add(day)
            lines.append(f"{day},{value:.6f}")
    (D / f"{sym}.csv").write_text("\n".join(lines) + "\n")
    print(f"PRICE {sym:8s} {len(lines) - 1:5d} rows  {lines[1].split(',')[0]} → {lines[-1].split(',')[0]}")
    time.sleep(0.5)
