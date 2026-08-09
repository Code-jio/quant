"""Futures symbol parsing and exchange-aware equivalence helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PRODUCT_EXCHANGE = {
    "IF": "CFFEX", "IC": "CFFEX", "IH": "CFFEX", "IM": "CFFEX",
    "T": "CFFEX", "TF": "CFFEX", "TS": "CFFEX", "TL": "CFFEX",
    "CU": "SHFE", "AU": "SHFE", "AG": "SHFE", "RB": "SHFE",
    "AL": "SHFE", "ZN": "SHFE", "PB": "SHFE", "NI": "SHFE",
    "SN": "SHFE", "FU": "SHFE", "BU": "SHFE", "HC": "SHFE",
    "RU": "SHFE", "SP": "SHFE", "SS": "SHFE", "AO": "SHFE",
    "SC": "INE", "NR": "INE", "BC": "INE", "LU": "INE",
    "A": "DCE", "B": "DCE", "C": "DCE", "CS": "DCE",
    "M": "DCE", "Y": "DCE", "P": "DCE", "L": "DCE",
    "V": "DCE", "PP": "DCE", "J": "DCE", "JM": "DCE",
    "I": "DCE", "EG": "DCE", "EB": "DCE", "PG": "DCE",
    "LH": "DCE",
    "CF": "CZCE", "SR": "CZCE", "TA": "CZCE", "MA": "CZCE",
    "OI": "CZCE", "RM": "CZCE", "ZC": "CZCE", "FG": "CZCE",
    "SA": "CZCE", "UR": "CZCE", "AP": "CZCE", "CJ": "CZCE",
    "PK": "CZCE", "PF": "CZCE", "PX": "CZCE", "SH": "CZCE",
    "SI": "GFEX", "LC": "GFEX",
}

KNOWN_EXCHANGES = frozenset(PRODUCT_EXCHANGE.values())


@dataclass(frozen=True)
class ParsedSymbol:
    contract: str
    exchange: str


def extract_product(symbol: Any) -> str:
    """Return the alphabetic product prefix from a bare contract."""
    match = re.match(r"([A-Za-z]+)", str(symbol or "").strip())
    return match.group(1).upper() if match else str(symbol or "").strip().upper()


def parse_symbol(symbol: Any) -> ParsedSymbol:
    """Parse bare, ``contract.EXCHANGE`` and ``EXCHANGE.contract`` forms."""
    raw = str(symbol or "").strip().replace(" ", "")
    if not raw:
        return ParsedSymbol("", "")

    contract = raw
    explicit_exchange = ""
    if "." in raw:
        left, right = raw.split(".", 1)
        if left.upper() in KNOWN_EXCHANGES:
            explicit_exchange = left.upper()
            contract = right
        elif right.upper() in KNOWN_EXCHANGES:
            explicit_exchange = right.upper()
            contract = left

    normalized_contract = contract.lower()
    inferred_exchange = PRODUCT_EXCHANGE.get(extract_product(contract), "")
    return ParsedSymbol(normalized_contract, explicit_exchange or inferred_exchange)


def symbols_match(left: Any, right: Any) -> bool:
    """Compare contracts without allowing a known exchange contradiction."""
    left_symbol = parse_symbol(left)
    right_symbol = parse_symbol(right)
    if not left_symbol.contract or left_symbol.contract != right_symbol.contract:
        return False
    if left_symbol.exchange and right_symbol.exchange:
        return left_symbol.exchange == right_symbol.exchange
    return not left_symbol.exchange and not right_symbol.exchange


def is_supported_symbol(symbol: Any) -> bool:
    """Return whether a contract product has one unambiguous known exchange."""
    parsed = parse_symbol(symbol)
    expected_exchange = PRODUCT_EXCHANGE.get(extract_product(parsed.contract), "")
    return bool(
        parsed.contract
        and expected_exchange
        and parsed.exchange == expected_exchange
    )


def symbol_key(symbol: Any) -> str:
    """Return a stable exchange-aware key for sets and dictionaries."""
    parsed = parse_symbol(symbol)
    if not parsed.contract:
        return ""
    return f"{parsed.contract}.{parsed.exchange}" if parsed.exchange else parsed.contract
