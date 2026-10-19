"""Search scope, not an assertion of an investor's permission to trade."""

import json
from pathlib import Path
import re

_POLICY = json.loads(
    (Path(__file__).resolve().parents[3] / "shared" / "domestic_futures_policy.json").read_text(encoding="utf-8")
)


def is_domestic_future(spec: dict) -> bool:
    exchange = str(spec.get("exchange", "")).upper()
    if exchange not in _POLICY["exchanges"] or spec.get("product") != "FUTURES":
        return False
    if spec.get("source") == "historical" or spec.get("tradable") is False or spec.get("is_trading") is False:
        return False
    match = re.fullmatch(r"([A-Za-z]+)([0-9]{3,4})", str(spec.get("symbol", "")))
    if not match or not 1 <= int(match[2][-2:]) <= 12:
        return False
    if exchange != "CZCE" and len(match[2]) != 4:
        return False
    # Commodity futures follow the gateway's type, so new listings are not lost
    # behind an outdated product catalog. CFFEX also distributes FX test symbols.
    return exchange != "CFFEX" or match[1].upper() in _POLICY["cffex_futures"]
