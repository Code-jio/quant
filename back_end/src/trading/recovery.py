"""Operator reconciliation. No dispatch, cancellation or automatic resume."""
from datetime import datetime
from typing import Any

from ..strategy import Order, OrderStatus, OrderType, Direction, OffsetFlag, Trade

STATUSES = {"0": OrderStatus.FILLED, "1": OrderStatus.PARTFILLED, "2": OrderStatus.CANCELLED,
            "3": OrderStatus.SUBMITTED, "4": OrderStatus.CANCELLED, "5": OrderStatus.CANCELLED}
OFFSETS = {"0": OffsetFlag.OPEN, "1": OffsetFlag.CLOSE, "3": OffsetFlag.CLOSE_TODAY, "4": OffsetFlag.CLOSE_YESTERDAY}


def order_status(row):
    return OrderStatus.REJECTED if row["OrderStatus"] == "5" and row.get("OrderSubmitStatus") == "4" else STATUSES[row["OrderStatus"]]


def broker_time(day, clock):
    return datetime.strptime(f"{day} {clock} +0800", "%Y%m%d %H:%M:%S %z")


def report(engine):
    book = getattr(engine.gateway, "reconciliation", None)
    snapshot: dict[str, Any] = book.snapshot(engine.gateway.trading_day) if book else {"status": "unsupported", "reason": "网关不支持完整查询"}
    differences = []
    if snapshot["status"] == "ready":
        remote = {row["order_id"]: row for row in snapshot["orders"]}
        for identity, row in remote.items():
            local = engine.gateway.orders.get(identity)
            if local is None or (local.symbol, local.direction.value, local.offset, local.price, local.volume,
                                 local.traded_volume, local.status) != (
                    row["InstrumentID"], "long" if row["Direction"] == "0" else "short", OFFSETS[row["CombOffsetFlag"]],
                    float(row["LimitPrice"]), int(row["VolumeTotalOriginal"]), int(row["VolumeTraded"]), order_status(row)):
                differences.append(identity)
        differences.extend(identity for identity, order in engine.gateway.orders.items() if order.is_active() and identity not in remote)
        known = {(t.trading_day, t.exchange, t.trade_id): t for t in engine.ledger.trades(10000)} if engine.ledger else {}
        for row in snapshot["trades"]:
            trade = known.get((snapshot["trading_day"], row.get("ExchangeID", ""), f"CTP.{row.get('TradeID')}"))
            if trade is None or (trade.symbol, trade.price, trade.volume, trade.direction.value, trade.offset) != (
                    row["InstrumentID"], float(row["Price"]), int(row["Volume"]),
                    "long" if row["Direction"] == "0" else "short", OFFSETS[row["OffsetFlag"]]):
                differences.append(f"trade:{row.get('TradeID')}")
    return {"broker_snapshot": snapshot, "differences": sorted(set(differences)),
            "pending_intents": engine.ledger.pending_intents() if engine.ledger else [],
            "reconciled": bool(snapshot["status"] == "ready" and engine.gateway.ready and not differences
                               and not engine.missing_order_replays and not (engine.ledger and engine.ledger.unresolved()))}


def checked_snapshot(engine, identity):
    if not engine.risk_manager.emergency_stop:
        raise ValueError("请先急停，再核对执行记录；不会自动撤单或恢复交易")
    book = getattr(engine.gateway, "reconciliation", None)
    snapshot = book.snapshot(engine.gateway.trading_day) if book else {}
    if snapshot.get("status") != "ready" or snapshot.get("id") != identity or not engine.gateway.ready:
        raise ValueError("需要当前账户的新鲜完整柜台快照")
    return snapshot


def import_snapshot(engine, snapshot):
    """Pre-validate every row before changing local records."""
    orders, trades, lookup = [], [], {}
    for row in snapshot["orders"]:
        order = Order(order_id=row["order_id"], symbol=row["InstrumentID"],
                      direction={"0": Direction.LONG, "1": Direction.SHORT}[row["Direction"]],
                      offset=OFFSETS[row["CombOffsetFlag"]], order_type={"2": OrderType.LIMIT, "1": OrderType.MARKET}[row["OrderPriceType"]],
                      price=float(row["LimitPrice"]), volume=int(row["VolumeTotalOriginal"]),
                      traded_volume=int(row["VolumeTraded"]), status=order_status(row),
                      create_time=broker_time(row["InsertDate"], row["InsertTime"]))
        previous = engine.gateway.orders.get(order.order_id)
        if previous and ((not previous.is_active() and order.is_active()) or order.traded_volume < previous.traded_volume):
            raise ValueError("柜台快照会回退已有委托状态，请重新核对")
        orders.append(order)
        lookup[(row.get("ExchangeID"), row.get("OrderSysID"))] = order.order_id
    for row in snapshot["trades"]:
        order_id = lookup.get((row.get("ExchangeID"), row.get("OrderSysID")))
        if not order_id or not row.get("TradeID"):
            raise ValueError("成交记录缺少可关联的柜台委托，暂不能导入")
        trades.append(Trade(trade_id=f"CTP.{row['TradeID']}", order_id=order_id, symbol=row["InstrumentID"],
                            direction={"0": Direction.LONG, "1": Direction.SHORT}[row["Direction"]],
                            offset=OFFSETS[row["OffsetFlag"]], price=float(row["Price"]), volume=int(row["Volume"]),
                            trade_time=broker_time(row["TradeDate"], row["TradeTime"]),
                            trading_day=snapshot["trading_day"], exchange=row.get("ExchangeID", ""),
                            account_id=engine.gateway.account.account_id, pnl_known=False, commission_known=False))
    for order in orders:
        engine.gateway.orders[order.order_id] = order
        engine.order_manager.update_order(order)
        if engine.ledger:
            engine.ledger.record_order(order)
        engine.missing_order_replays.discard(order.order_id)
    for trade in trades:
        if engine.ledger and engine.ledger.record_trade(trade):
            engine.trades[(trade.account_id, trade.trading_day, trade.exchange, trade.trade_id)] = trade
    return {"orders": len(orders), "trades": len(trades)}


def resolve_intent(engine, identity, snapshot_id, order_id, note):
    snapshot = checked_snapshot(engine, snapshot_id)
    if not engine.ledger or len(note.strip()) < 12:
        raise ValueError("请填写至少 12 个字符的核对依据或外部凭证编号")
    intent = next((row for row in engine.ledger.pending_intents() if row["id"] == identity), None)
    if intent is None or intent.get("_trading_day") != snapshot["trading_day"]:
        raise ValueError("执行意图不属于本交易日；历史未决记录需要历史柜台凭证，不能用当日查询清除")
    candidates = [row for row in snapshot["orders"] if row["InstrumentID"] == intent["symbol"]]
    if order_id:
        row = next((item for item in candidates if item["order_id"] == order_id), None)
        if row is None or (row["Direction"] != ("0" if intent["direction"] == "long" else "1")
                           or OFFSETS.get(row["CombOffsetFlag"], None) != OffsetFlag(intent["offset"])
                           or float(row["LimitPrice"]) != intent["price"]
                           or int(row["VolumeTotalOriginal"]) != intent["volume"]):
            raise ValueError("柜台委托与执行意图参数不一致")
    else:
        # Absence alone is insufficient: require a quiet interval, complete queries
        # after the intent, no same-symbol ambiguity, and the operator's evidence.
        created = datetime.fromisoformat(intent["_created_at"])
        queried = datetime.fromisoformat(snapshot["requested_at"])
        if (queried - created).total_seconds() < 30 or candidates or any(
                row["InstrumentID"] == intent["symbol"] for row in snapshot["trades"]):
            raise ValueError("存在同合约记录或查询过早，不能确认未发送；请核对柜台凭证")
    import_snapshot(engine, snapshot)
    # A recovered conditional trigger stays paused when confirmed unsent.
    # It must never be re-armed merely by resolving its execution journal entry.
    from .order_manager import PreOrderStatus
    for condition in engine.order_manager.pre_orders.values():
        if condition.status == PreOrderStatus.UNKNOWN and f"由预埋单触发: {condition.pre_order_id} " in intent.get("comment", ""):
            condition.related_order_id = order_id
            condition.status = PreOrderStatus.TRIGGERED if order_id else PreOrderStatus.PAUSED
            engine.ledger.save_pre_order(condition)
    engine.ledger.resolve_intent(identity, order_id, {"snapshot_id": snapshot_id, "order_id": order_id,
                                "note": note.strip(), "broker_snapshot": snapshot})
