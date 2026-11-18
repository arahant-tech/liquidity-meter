"""Download the inputs for this study into scripts/data/ (never committed).

Usage: python fetch.py <study scripts directory>
"""
import json
import subprocess
import sys
import time
from pathlib import Path

D = Path(sys.argv[1]) / "data"
D.mkdir(parents=True, exist_ok=True)


def curl(url, *extra):
    return subprocess.run(["curl", "-sS", "--fail", "--max-time", "60", *extra, url],
                          capture_output=True, text=True, check=True).stdout


for sid in ("WALCL", "WRBWFRBL", "WDTGAL", "WLRRAOL", "DTB3", "VIXCLS", "DFII10"):
    (D / f"{sid}.csv").write_text(curl(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2002-12-01"))
    time.sleep(0.5)
for sym in ("QQQ", "SPY"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=1038700800&period2=1794787200&interval=1d&events=div%2Csplits")
    res = json.loads(curl(url, "-A", "Mozilla/5.0"))["chart"]["result"][0]
    lines = ["date,close"]
    for stamp, value in zip(res["timestamp"], res["indicators"]["adjclose"][0]["adjclose"]):
        if value is not None:
            lines.append(time.strftime("%Y-%m-%d", time.gmtime(stamp + 5 * 3600)) + f",{value:.6f}")
    (D / f"{sym}.csv").write_text("\n".join(lines) + "\n")
    time.sleep(0.5)
print("done")
