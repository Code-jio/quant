"""
回测配置模块
"""

from dataclasses import dataclass


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
