"""Command line for the verification record.

  python -m lm.ledger verify    --dir ledger
  python -m lm.ledger seal      --dir ledger --judgment judgment.json
  python -m lm.ledger score     --dir ledger --history data/history --runtime .private/history
  python -m lm.ledger scorecard --dir ledger --output data/public/ledger.json
"""
import argparse
import json
from pathlib import Path
import sys

from lm import catalog
from lm.collect import live_api
from lm.ledger import chain, score, seal
from lm.ledger.chain import LedgerError
from lm.store import history

PROTOCOLS = catalog.ROOT / "research" / "protocols"


def scoring_plan(protocols):
    return {pid: entry["scoring"] for pid, entry in protocols.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("verify", "seal", "score", "scorecard"))
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--judgment", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = {name: {"records": len(chain.read(args.dir / name)), "head": chain.head(args.dir / name)}
                      for name in (seal.RECORDS, score.SCORES)}
        elif args.command == "seal":
            record, reason = seal.seal(args.dir, json.loads(args.judgment.read_text()),
                                       protocols=seal.load_protocols(PROTOCOLS))
            result = {"sealed": record is not None, "reason": reason,
                      "record": seal.record_hash(record) if record else None,
                      "head": chain.head(args.dir / seal.RECORDS)}
        elif args.command == "score":
            protocols = seal.load_protocols(PROTOCOLS)
            persisted = {s["series_id"]: s["persist"] for s in catalog.history()["series"]}
            assets = {item["asset"] for items in scoring_plan(protocols).values() for item in items}
            prices = {a: history.read_series(args.history, args.runtime, a, persisted[a]) for a in assets}
            added = score.due(args.dir, prices, scoring_plan(protocols))
            result = {"scores_added": len(added), "head": chain.head(args.dir / score.SCORES),
                      "assets_without_prices": sorted(a for a, rows in prices.items() if not rows)}
        else:
            card = score.scorecard(args.dir, scoring_plan(seal.load_protocols(PROTOCOLS)))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            live_api._atomic_json(args.output, card)
            result = {"written": str(args.output), "protocols": list(card["protocols"])}
    except LedgerError as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
