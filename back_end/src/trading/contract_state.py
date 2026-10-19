"""Broker instrument lifecycle; unknown is not a tradability certification."""
from datetime import datetime


def trading_state(metadata, trading_day):
    raw = metadata.get("IsTrading")
    active = bool(raw) if isinstance(raw, (bool, int)) and raw in (0, 1) else None
    dates = {}
    for key in ("OpenDate", "ExpireDate"):
        value = str(metadata.get(key) or "")
        try:
            dates[key] = datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") if len(value) == 8 else None
        except ValueError:
            dates[key] = None
    day = str(trading_day or "")
    outside = bool(len(day) == 8 and (
        (dates["OpenDate"] and day < dates["OpenDate"])
        or (dates["ExpireDate"] and day > dates["ExpireDate"])
    ))
    tradable = False if active is False or outside else (
        True if active is True and all(dates.values()) and len(day) == 8 else None
    )
    return {"is_trading": active, "listed_date": dates["OpenDate"], "expiry_date": dates["ExpireDate"],
            "tradable": tradable, "trading_status": "eligible" if tradable else ("inactive" if tradable is False else "unknown")}
