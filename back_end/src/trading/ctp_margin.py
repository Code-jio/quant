"""Account/session-local CTP margin queries, serialized by the gateway timer."""
from collections import deque
from datetime import datetime
import logging
from math import isfinite
import threading
import time

logger = logging.getLogger(__name__)
RATE_FIELDS = (
    "LongMarginRatioByMoney", "ShortMarginRatioByMoney",
    "LongMarginRatioByVolume", "ShortMarginRatioByVolume",
)


class MarginRateBook:
    def __init__(self, specs):
        self.specs = specs
        self.records: dict[str, dict] = {}
        self.queue: deque[str] = deque()
        self.pending: dict | None = None
        self.day = ""
        self.lock = threading.RLock()

    def _clear_values(self, symbol):
        spec = self.specs.get(symbol, {})
        if spec.get("margin_source") == "ctp_query":
            for key in list(spec):
                if "margin" in key:
                    spec.pop(key)
            spec["margin_rate"] = None

    def invalidate(self):
        with self.lock:
            for symbol in self.records:
                self._clear_values(symbol)
            self.records.clear()
            self.queue.clear()
            self.pending = None

    def _check_day(self, day):
        if day != self.day:
            self.invalidate()
            self.day = day

    def expire(self, day):
        with self.lock:
            self._check_day(day)
            now = time.monotonic()
            for symbol, record in self.records.items():
                if record["status"] == "ready" and now - record["clock"] >= 1800:
                    self._clear_values(symbol)
                    if len(self.queue) < 64:
                        record.update(status="pending", phase="instrument", reason="正在刷新账户保证金", clock=now)
                        self.queue.append(symbol)
                    else:
                        record.update(status="error", reason="保证金数据已过期，等待重新查询", clock=now - 30)

    def request(self, symbol, day):
        with self.lock:
            self._check_day(day)
            spec = self.specs.get(symbol)
            if not spec or spec.get("product") != "FUTURES":
                raise ValueError("仅支持柜台已确认的单腿期货合约保证金查询")
            if not day:
                raise ValueError("柜台交易日尚未就绪")
            record = self.records.get(symbol)
            if record:
                age = time.monotonic() - record["clock"]
                if record["status"] == "pending" or age < (1800 if record["status"] == "ready" else 30):
                    return self._public(record)
            if len(self.queue) >= 64:
                raise ValueError("保证金查询队列已满，请稍后重试")
            self._clear_values(symbol)
            record = {"symbol": symbol, "status": "pending", "trading_day": day,
                      "clock": time.monotonic(), "phase": "instrument", "reason": "正在查询账户保证金"}
            self.records[symbol] = record
            self.queue.append(symbol)
            return self._public(record)

    @staticmethod
    def _public(record):
        return {k: v for k, v in record.items() if k not in {"clock", "phase", "base_adjustment"}}

    def _error(self, symbol, reason):
        self._clear_values(symbol)
        self.records[symbol].update(status="error", reason=reason, clock=time.monotonic())
        self.pending = None
        logger.warning("CTP margin query %s: %s", symbol, reason)

    def dispatch(self, td, day):
        """True means a query is in flight/sent; do not send another query then."""
        with self.lock:
            self._check_day(day)
            if self.pending:
                if time.monotonic() - self.pending["clock"] > 10:
                    self._error(self.pending["symbol"], "柜台保证金查询超时")
                return True
            if not self.queue:
                return False
            symbol = self.queue.popleft()
            record = self.records[symbol]
            phase = record["phase"]
            request = {"BrokerID": td.brokerid, "InstrumentID": symbol,
                       "ExchangeID": self.specs[symbol]["exchange"], "HedgeFlag": "1"}
            if phase == "instrument":
                request["InvestorID"] = td.userid
            td.reqid += 1
            self.pending = {"symbol": symbol, "phase": phase, "reqid": td.reqid,
                            "broker": td.brokerid, "investor": td.userid, "day": day,
                            "clock": time.monotonic(), "rows": []}
            try:
                method = td.reqQryInstrumentMarginRate if phase == "instrument" else td.reqQryExchangeMarginRate
                code = method(request, td.reqid)
                if code != 0:
                    self._error(symbol, f"柜台保证金查询发送失败：{code}")
            except Exception:
                self._error(symbol, "柜台保证金查询接口调用失败")
            return True

    def on_response(self, event, day):
        with self.lock:
            self._check_day(day)
            pending = self.pending
            if not pending or event.get("reqid") != pending["reqid"] or event.get("query") != pending["phase"]:
                return
            symbol = pending["symbol"]
            if event.get("error_id"):
                self._error(symbol, f"柜台保证金查询错误：{event['error_id']}")
                return
            data = event.get("data") or {}
            if data.get("InstrumentID"):
                pending["rows"].append(data)
            if len(pending["rows"]) > 1:
                self._error(symbol, "柜台返回多条保证金记录，无法确定适用值")
                return
            if not event.get("last"):
                return
            if not pending["rows"]:
                self._error(symbol, "柜台未返回该合约保证金")
                return
            row = pending["rows"][0]
            try:
                if row["InstrumentID"] != symbol or row.get("HedgeFlag") != "1":
                    raise ValueError("合约或投机套保标志不匹配")
                if row.get("BrokerID") not in (None, "", pending["broker"]):
                    raise ValueError("经纪商不匹配")
                if row.get("InvestorID") not in (None, "", pending["investor"]):
                    raise ValueError("账户不匹配")
                if row.get("ExchangeID") not in (None, "", self.specs[symbol]["exchange"]):
                    raise ValueError("交易所不匹配")
                values = [float(row[key]) for key in RATE_FIELDS]
                if any(not isfinite(v) or v < 0 for v in values) or any(v > 1 for v in values[:2]):
                    raise ValueError("保证金数值无效")
                record = self.records[symbol]
                if pending["phase"] == "instrument":
                    relative = row["IsRelative"]
                    if relative not in (0, 1):
                        raise ValueError("保证金计费方式未知")
                    if relative:
                        record.update(phase="exchange", base_adjustment=values)
                        self.pending = None
                        self.queue.appendleft(symbol)
                        return
                else:
                    values = [a + b for a, b in zip(values, record["base_adjustment"])]
                if (any(not isfinite(v) for v in values) or any(v > 1 for v in values[:2])
                        or not (values[0] + values[2]) or not (values[1] + values[3])):
                    raise ValueError("保证金有效值缺失或超出支持范围")
            except (KeyError, TypeError, ValueError):
                self._error(symbol, "柜台保证金字段不完整、无效或与查询身份不匹配")
                return

            long_rate, short_rate, long_fixed, short_fixed = values
            fields = {
                "margin_rate": max(long_rate, short_rate), "margin_per_lot": max(long_fixed, short_fixed),
                "long_margin_rate": long_rate, "short_margin_rate": short_rate,
                "long_margin_per_lot": long_fixed, "short_margin_per_lot": short_fixed,
                "margin_source": "ctp_query", "margin_trading_day": day,
                "margin_updated_at": datetime.now().astimezone().isoformat(),
            }
            self.specs[symbol].update(fields)
            record.update(fields, status="ready", reason="", clock=time.monotonic())
            record.pop("base_adjustment", None)
            self.pending = None
            logger.info("CTP margin verified symbol=%s day=%s long=%s short=%s long_fixed=%s short_fixed=%s",
                        symbol, day, long_rate, short_rate, long_fixed, short_fixed)
