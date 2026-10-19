"""
回测配置模块
"""

from dataclasses import dataclass
from datetime import date
import math


@dataclass
class BacktestConfig:
    """回测配置"""

    start_date: str = "2023-01-01"
    end_date: str = "2024-01-01"
    initial_capital: float = 1000000.0
    commission_rate: float = 0.0003
    slip_rate: float = 0.0001
    margin_rate: float = 0.12
    contract_multiplier: float = 1.0
    max_errors: int = 100
    timeframe: str = "1d"

    def __post_init__(self):
        if date.fromisoformat(self.start_date) > date.fromisoformat(self.end_date):
            raise ValueError("start_date must not exceed end_date")
        for name in ("initial_capital", "contract_multiplier", "margin_rate"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("commission_rate", "slip_rate"):
            if not math.isfinite(getattr(self, name)) or not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be within [0,1]")
        if self.margin_rate > 1 or not 1 <= self.max_errors <= 1000:
            raise ValueError("Invalid margin_rate or max_errors")
        if self.timeframe not in {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}:
            raise ValueError("Invalid timeframe")
