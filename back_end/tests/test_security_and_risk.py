import os
import sys
import unittest
from datetime import datetime, timedelta


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.api.security import SessionStore
from src.api import LoginRequest
from src.strategy import Direction, OffsetFlag, OrderType, Position, Signal
from src.trading.risk import RiskManager
from main import DEFAULT_CONFIG


class SafeDefaultsTest(unittest.TestCase):
    def test_ctp_defaults_do_not_embed_production_credentials(self):
        request = LoginRequest(username="u", password="p")

        self.assertEqual(request.td_server, "")
        self.assertEqual(request.md_server, "")
        self.assertEqual(request.app_id, "")
        self.assertEqual(request.auth_code, "")
        self.assertEqual(request.environment, "测试")
        self.assertEqual(DEFAULT_CONFIG["trading"]["auth_code"], "")
        self.assertEqual(DEFAULT_CONFIG["trading"]["vnpy_environment"], "测试")


class SessionStoreTest(unittest.TestCase):
    def test_session_create_validate_and_revoke(self):
        store = SessionStore(ttl=timedelta(minutes=5))

        token = store.create()

        self.assertTrue(store.is_valid(token))
        self.assertTrue(store.has_active_sessions())

        store.revoke(token)

        self.assertFalse(store.is_valid(token))
        self.assertFalse(store.has_active_sessions())

    def test_expired_session_is_rejected(self):
        store = SessionStore(ttl=timedelta(seconds=-1))

        token = store.create()

        self.assertFalse(store.is_valid(token))
        self.assertFalse(store.has_active_sessions())


class RiskManagerTest(unittest.TestCase):
    def _signal(self, **overrides):
        data = {
            "symbol": "rb2505",
            "datetime": datetime.now(),
            "direction": Direction.LONG,
            "price": 0,
            "volume": 1,
            "order_type": OrderType.MARKET,
            "offset": OffsetFlag.OPEN,
        }
        data.update(overrides)
        return Signal(**data)

    def test_rejects_order_volume_above_limit(self):
        manager = RiskManager({"max_order_volume": 2})

        result = manager.check_signal(self._signal(volume=3))

        self.assertFalse(result.allowed)
        self.assertIn("volume", result.reason.lower())

    def test_configure_reads_nested_risk_config(self):
        manager = RiskManager()
        manager.configure({"gateway": "vnpy", "risk": {"max_order_volume": 1}})

        result = manager.check_signal(self._signal(volume=2))

        self.assertFalse(result.allowed)
        self.assertIn("volume", result.reason.lower())

    def test_rejects_close_without_position(self):
        manager = RiskManager()

        result = manager.check_signal(self._signal(offset=OffsetFlag.CLOSE), positions={})

        self.assertFalse(result.allowed)
        self.assertIn("No position", result.reason)

    def test_allows_valid_open_order(self):
        manager = RiskManager({"max_order_volume": 2})

        result = manager.check_signal(self._signal(volume=2), positions={})

        self.assertTrue(result.allowed)

    def test_rejects_close_volume_above_position(self):
        manager = RiskManager()
        positions = {"rb2505": Position(symbol="rb2505", direction=Direction.NET, volume=1)}

        result = manager.check_signal(
            self._signal(offset=OffsetFlag.CLOSE, volume=2),
            positions=positions,
        )

        self.assertFalse(result.allowed)
        self.assertIn("Close volume", result.reason)

    def test_emergency_stop_rejects_all_orders(self):
        manager = RiskManager()
        manager.set_emergency_stop(True, "manual halt")

        result = manager.check_signal(self._signal(), positions={})

        self.assertFalse(result.allowed)
        self.assertIn("Emergency stop", result.reason)

    def test_rejects_stale_market_data_before_opening(self):
        manager = RiskManager({"max_market_data_age_seconds": 1})
        stale_tick = {
            "last_price": 3888.0,
            "timestamp": datetime.now() - timedelta(seconds=3),
        }

        result = manager.check_signal(self._signal(price=3888), positions={}, market_data=stale_tick)

        self.assertFalse(result.allowed)
        self.assertIn("Market data is stale", result.reason)

    def test_rejects_limit_order_far_from_latest_price(self):
        manager = RiskManager({"max_price_deviation": 0.01})
        tick = {"last_price": 100.0, "timestamp": datetime.now()}

        result = manager.check_signal(
            self._signal(price=103.0, order_type=OrderType.LIMIT),
            positions={},
            market_data=tick,
        )

        self.assertFalse(result.allowed)
        self.assertIn("Price deviation", result.reason)

    def test_rejects_order_value_above_limit(self):
        manager = RiskManager({"max_order_value": 10_000, "default_contract_multiplier": 10})
        tick = {"last_price": 101.0, "timestamp": datetime.now()}

        result = manager.check_signal(self._signal(price=101.0, volume=10), positions={}, market_data=tick)

        self.assertFalse(result.allowed)
        self.assertIn("Order value", result.reason)

    def test_duplicate_signal_is_rejected_within_window(self):
        manager = RiskManager({"duplicate_signal_window_seconds": 60})
        signal = self._signal(price=100.0, order_type=OrderType.LIMIT)

        first = manager.check_signal(signal, positions={}, market_data={"last_price": 100.0, "timestamp": datetime.now()})
        manager.record_order(signal)
        second = manager.check_signal(signal, positions={}, market_data={"last_price": 100.0, "timestamp": datetime.now()})

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertIn("Duplicate signal", second.reason)


if __name__ == "__main__":
    unittest.main()
