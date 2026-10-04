"""Isolated browser fixture server. No native gateway or external connection."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["QUANT_ENV"] = "test"
os.environ["QUANT_ALLOW_SYNTHETIC_DATA"] = "1"
os.environ["QUANT_CORS_ORIGINS"] = "http://127.0.0.1:" + os.getenv("PLAYWRIGHT_PORT", "55173")


def main():
    import pandas as pd
    import uvicorn
    from src.api import create_app
    from src.data.db import DatabaseManager
    from src.trading.types import AccountInfo, TradingStatus
    from src.trading.gateway import GatewayBase
    import src.trading

    class BrowserGateway(GatewayBase):
        def __init__(self):
            super().__init__("E2E_OFFLINE")

        def connect(self, config):
            self.status = TradingStatus.CONNECTED
            self.account = AccountInfo(account_id="E2E_ONLY", balance=100000, available=100000)
            return True

        def disconnect(self):
            self.status = TradingStatus.STOPPED

        def send_order(self, signal):
            raise RuntimeError("E2E fixture never sends orders")

        def cancel_order(self, order_id):
            return False

        def query_account(self):
            return self.account

        def query_positions(self):
            return []

        def query_orders(self):
            return []

    src.trading.create_gateway = lambda *_args: BrowserGateway()
    original = Path.cwd()
    with TemporaryDirectory(prefix="quant-browser-") as folder:
        os.chdir(folder)
        try:
            frame = pd.DataFrame(
                {
                    "datetime": pd.date_range("2024-01-01", periods=100),
                    "open": [100 + i for i in range(100)],
                    "high": [102 + i for i in range(100)],
                    "low": [99 + i for i in range(100)],
                    "close": [101 + i for i in range(100)],
                    "volume": 1000,
                }
            )
            DatabaseManager().save_bars(frame, "E2E2026", "1d", data_source="e2e_fixture")
            uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="warning", timeout_graceful_shutdown=2)
        finally:
            os.chdir(original)


if __name__ == "__main__":
    main()
