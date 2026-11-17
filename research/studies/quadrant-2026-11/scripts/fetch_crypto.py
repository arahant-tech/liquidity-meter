import json, subprocess, sys, time
from pathlib import Path
D = Path(sys.argv[1]) / "data"
for sym in ("BTC-USD", "ETH-USD"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=1388534400&period2=1794787200&interval=1d"
    body = subprocess.run(["curl", "-sS", "--fail", "--max-time", "60", "-A", "Mozilla/5.0", url], capture_output=True, text=True, check=True).stdout
    res = json.loads(body)["chart"]["result"][0]
    ts, close = res["timestamp"], res["indicators"]["quote"][0]["close"]
    lines = ["date,close"]; seen = set()
    for t, c in zip(ts, close):
        day = time.strftime("%Y-%m-%d", time.gmtime(t))
        if c is not None and day not in seen:
            seen.add(day); lines.append(f"{day},{c:.4f}")
    (D / f"{sym}.csv").write_text("\n".join(lines) + "\n")
    print(f"{sym} {len(lines)-1} rows {lines[1].split(',')[0]} → {lines[-1].split(',')[0]}")
    time.sleep(0.4)
