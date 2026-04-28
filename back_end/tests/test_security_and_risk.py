import os
import sys
import unittest
from datetime import datetime, timedelta


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.api.security import SessionStore
from src.strategy import Direction, OffsetFlag, OrderType, Position, Signal
from src.trading.risk import RiskManager


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


if __name__ == "__main__":
    unittest.main()
