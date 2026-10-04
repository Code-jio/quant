"""Capture structured CTP lifecycle/query results omitted by vn.py's data objects.

Installed before connect; updates are queued on the same EventEngine as orders.
No success is inferred from log messages. Unsupported callback APIs fail closed.
"""

EVENT_SNAPSHOT = "eQuantSnapshot"


def install_observer(gateway, emit):
    td, md = gateway.td_api, gateway.md_api
    raw_account = {}
    position_keys = set()
    trading_day = {"value": ""}

    def wrap(obj, name, after=None, before=None):
        original = getattr(obj, name)

        def callback(*args):
            if before:
                before(*args)
            result = original(*args)
            if after:
                after(*args)
            return result

        setattr(obj, name, callback)

    for api, kind in ((td, "td"), (md, "md")):
        wrap(api, "onFrontDisconnected", after=lambda *_args, k=kind: emit({"kind": k, "ready": False}))

        def login(data, error, *args, k=kind):
            ok = not error.get("ErrorID", 0)
            if ok and data.get("TradingDay"):
                trading_day["value"] = data["TradingDay"]
            emit({"kind": k, "ready": ok, "trading_day": trading_day["value"]})

        wrap(api, "onRspUserLogin", after=login)

    wrap(
        td,
        "onRspSettlementInfoConfirm",
        after=lambda data, error, *_: emit({"kind": "settlement", "ready": not error.get("ErrorID", 0)}),
    )
    wrap(
        td,
        "onRspQryInstrument",
        after=lambda data, error, reqid, last: (
            emit({"kind": "contracts", "ready": not error.get("ErrorID", 0)}) if last else None
        ),
    )

    def account_raw(data, error, *_):
        raw_account.clear()
        if not error.get("ErrorID", 0):
            raw_account.update(data)
            raw_account.setdefault("TradingDay", trading_day["value"])

    wrap(td, "onRspQryTradingAccount", before=account_raw)

    def attach_account(account):
        account.extra = {**(getattr(account, "extra", None) or {}), **raw_account}

    wrap(gateway, "on_account", before=attach_account)

    def collect_position(position):
        side = getattr(position.direction, "name", "").lower()
        position_keys.add(f"{position.symbol}_{side}")

    wrap(gateway, "on_position", before=collect_position)

    def positions_done(data, error, reqid, last):
        if last:
            emit({"kind": "positions", "keys": sorted(position_keys), "ready": not error.get("ErrorID", 0)})
            position_keys.clear()

    wrap(td, "onRspQryInvestorPosition", after=positions_done)

    def attach_day(value):
        value.extra = {**(getattr(value, "extra", None) or {}), "TradingDay": trading_day["value"]}

    wrap(gateway, "on_trade", before=attach_day)
    wrap(gateway, "on_tick", before=attach_day)
