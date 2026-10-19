"""Explicit, local import and recovery commands; never connects to a broker."""

import argparse
import json
from pathlib import Path
import pandas as pd
from .runtime import backup_database, restore_database, InstanceLock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("backup", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument("source")
        sub.add_argument("destination")
    sub = commands.add_parser("import-bars")
    sub.add_argument("csv")
    sub.add_argument("--symbol", required=True)
    sub.add_argument("--timeframe", choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"], required=True)
    sub.add_argument("--source", required=True)
    sub.add_argument("--database", default="data/historical/quotes.db")
    sub.add_argument("--adjustment", default="raw")
    sub.add_argument("--rollover-rule", default="none")
    args = parser.parse_args()
    if args.command == "import-bars":
        from .data.db import DatabaseManager

        if args.source.lower() in {"synthetic", "simulated", "unknown", "mixed"}:
            raise ValueError("Historical import requires an identified real data source")
        frame = pd.read_csv(args.csv)
        DatabaseManager(args.database).save_bars(
            frame,
            args.symbol,
            args.timeframe,
            data_source=args.source,
            adjustment=args.adjustment,
            rollover_rule=args.rollover_rule,
        )
        print(
            json.dumps({"rows": len(frame), "symbol": args.symbol, "timeframe": args.timeframe, "source": args.source})
        )
    elif args.command == "backup":
        backup_database(args.source, args.destination)
        print("Backup verified:", Path(args.destination).resolve())
    else:
        owner = InstanceLock("data/runtime/executor.lock")
        owner.acquire()
        try:
            restore_database(args.source, args.destination)
        finally:
            owner.release()
        print("Restored to new database:", Path(args.destination).resolve())


if __name__ == "__main__":
    main()
