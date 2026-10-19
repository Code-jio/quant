"""Read-only full-day CTP queries. Query completion never resolves an intent."""
from copy import deepcopy
from datetime import datetime
import threading
import time
import uuid
from math import isfinite
from typing import Any


class BrokerReconciliation:
    def __init__(self):
        self.lock = threading.RLock()
        self.pending = None
        self.queue = []
        self.result: dict[str, Any] = {"status": "idle", "reason": "尚未查询柜台委托和成交"}
        self.clock = 0.0

    def invalidate(self, reason="柜台状态变化，请重新查询"):
        with self.lock:
            self.pending = None
            self.queue = []
            self.result.update(status="stale", reason=reason)

    def start(self, day):
        with self.lock:
            if self.result["status"] == "pending":
                return self.snapshot(day)
            self.clock = time.monotonic()
            self.result = {"id": uuid.uuid4().hex, "status": "pending", "reason": "正在查询柜台",
                           "trading_day": day, "orders": [], "trades": [], "completed": [],
                           "requested_at": datetime.now().astimezone().isoformat()}
            self.queue = ["orders", "trades"]
            self.pending = None
            return self.snapshot(day)

    def snapshot(self, day):
        with self.lock:
            if self.result.get("trading_day", day) != day:
                self.invalidate("交易日变化，请重新查询")
            if self.result["status"] in ("pending", "ready") and time.monotonic() - self.clock >= 30:
                self.invalidate("柜台查询超时或快照已过期")
            return deepcopy(self.result)

    def dispatch(self, td, day):
        with self.lock:
            if self.snapshot(day)["status"] != "pending":
                return False
            if self.pending:
                return True
            query = self.queue.pop(0)
            td.reqid += 1
            self.pending = {"reqid": td.reqid, "query": query, "broker": td.brokerid, "investor": td.userid}
            try:
                method = td.reqQryOrder if query == "orders" else td.reqQryTrade
                code = method({"BrokerID": td.brokerid, "InvestorID": td.userid}, td.reqid)
                if code != 0:
                    self.invalidate(f"柜台查询发送失败：{code}")
            except Exception:
                self.invalidate("柜台查询接口调用失败")
            return True

    def on_response(self, event, day):
        with self.lock:
            if self.snapshot(day)["status"] != "pending" or not self.pending:
                return
            if any(event.get(key) != self.pending[key] for key in ("reqid", "query")):
                return
            if event.get("error_id"):
                self.invalidate(f"柜台查询错误：{event['error_id']}")
                return
            query = self.pending["query"]
            data = event.get("data") or {}
            if data.get("InstrumentID"):
                if (data.get("BrokerID", self.pending["broker"]) != self.pending["broker"]
                        or data.get("InvestorID", self.pending["investor"]) != self.pending["investor"]
                        or data.get("TradingDay", day) not in ("", day)):
                    self.invalidate("柜台回报账户或交易日不匹配")
                    return
                # Account credentials/identity fields from native responses never leave this adapter.
                keys = ("InstrumentID", "ExchangeID", "FrontID", "SessionID", "OrderRef", "OrderSysID",
                        "OrderStatus", "Direction", "CombOffsetFlag", "OffsetFlag", "LimitPrice",
                        "VolumeTotalOriginal", "VolumeTraded", "TradeID", "Price", "Volume", "TradingDay",
                        "InsertDate", "InsertTime", "TradeDate", "TradeTime", "OrderPriceType", "OrderSubmitStatus")
                row = {key: data[key] for key in keys if key in data}
                if any(isinstance(value, float) and not isfinite(value) for value in row.values()):
                    self.invalidate("柜台数值不是有限值")
                    return
                required: tuple[str, ...]
                if query == "orders":
                    required = ("FrontID", "SessionID", "OrderRef", "OrderStatus", "Direction", "CombOffsetFlag",
                                "LimitPrice", "VolumeTotalOriginal", "VolumeTraded", "OrderPriceType", "InsertDate", "InsertTime", "ExchangeID")
                    if any(key not in row for key in required) or row["OrderStatus"] not in ("0", "1", "2", "3", "4", "5"):
                        self.invalidate("柜台委托字段或状态不完整")
                        return
                    row["order_id"] = f"CTP.{row['FrontID']}_{row['SessionID']}_{row['OrderRef']}"
                    try:
                        total, traded = float(row["VolumeTotalOriginal"]), float(row["VolumeTraded"])
                        price = float(row["LimitPrice"])
                        valid = (isfinite(price) and price >= 0 and total.is_integer() and total > 0
                                 and traded.is_integer() and 0 <= traded <= total
                                 and row["Direction"] in ("0", "1") and row["CombOffsetFlag"] in ("0", "1", "3", "4")
                                 and row["OrderPriceType"] in ("1", "2"))
                    except (ValueError, TypeError, OverflowError):
                        valid = False
                    if not valid:
                        self.invalidate("柜台委托数值或方向无效")
                        return
                else:
                    required = ("ExchangeID", "OrderSysID", "TradeID", "Direction", "OffsetFlag", "Price", "Volume", "TradeDate", "TradeTime")
                    if any(not row.get(key) for key in required):
                        self.invalidate("柜台成交记录不完整")
                        return
                    try:
                        volume, price = float(row["Volume"]), float(row["Price"])
                        valid = (volume.is_integer() and volume > 0 and isfinite(price) and price > 0
                                 and row["Direction"] in ("0", "1") and row["OffsetFlag"] in ("0", "1", "3", "4"))
                    except (ValueError, TypeError, OverflowError):
                        valid = False
                    if not valid:
                        self.invalidate("柜台成交数值无效")
                        return
                try:
                    prefix = "Insert" if query == "orders" else "Trade"
                    datetime.strptime(f"{row[prefix + 'Date']} {row[prefix + 'Time']}", "%Y%m%d %H:%M:%S")
                except (KeyError, ValueError, TypeError):
                    self.invalidate("柜台回报时间无效")
                    return
                self.result[query].append(row)
                if len(self.result[query]) > 100000:
                    self.invalidate("柜台快照超过容量限制，不能确认完整性")
                    return
            if event.get("last"):
                self.result["completed"].append(query)
                self.pending = None
                if not self.queue:
                    self.clock = time.monotonic()
                    self.result.update(status="ready", reason="柜台查询完整，待核对本地执行记录",
                                       completed_at=datetime.now().astimezone().isoformat())
