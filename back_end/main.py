"""
量化交易系统主入口
"""

import os
import sys
import json
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data import DataManager
from src.strategy import create_strategy
from src.backtest import BacktestEngine, BacktestConfig
from src.trading import TradingEngine, create_gateway
from src.analysis import Analyzer
from src.settings import ctp_defaults


logger = logging.getLogger(__name__)
_CTP_DEFAULTS = ctp_defaults()


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


DEFAULT_CONFIG_PATH = "config/config_production.json"

DEFAULT_CONFIG = {
    "mode": "backtest",
    "backtest": {
        "start_date": "2023-01-01",
        "end_date": "2024-12-31",
        "initial_capital": 1000000,
        "commission_rate": 0.0003,
        "slip_rate": 0.0001,
        "margin_rate": 0.12,
        "contract_multiplier": 1,
    },
    "strategy": {
        "name": "ma_cross",
        "symbol": "IF9999",
        "fast_period": 10,
        "slow_period": 20,
        "position_ratio": 0.8,
        "max_errors": 10,
    },
    "trading": {
        "gateway": "vnpy",
        "username": "",
        "password": "",
        "broker_id": _CTP_DEFAULTS["broker_id"],
        "td_server": _CTP_DEFAULTS["td_server"],
        "md_server": _CTP_DEFAULTS["md_server"],
        "app_id": _CTP_DEFAULTS["app_id"],
        "auth_code": _CTP_DEFAULTS["auth_code"],
        "vnpy_environment": _CTP_DEFAULTS["vnpy_environment"],
        "initial_capital": 1000000,
    },
    "risk": {
        "enabled": True,
        "max_order_volume": 1000,
        "max_position_volume": 10000,
        "max_active_orders": 200,
        "max_orders_per_minute": 120,
        "max_daily_loss_ratio": 0.10,
        "allow_market_orders": False,
        "allowed_symbols": [],
        "blocked_symbols": [],
    },
}


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """加载配置文件，不存在时使用默认配置"""
    if not os.path.exists(config_path):
        logger.warning(f"配置文件不存在: {config_path}，使用内置默认配置")
        import copy

        return copy.deepcopy(DEFAULT_CONFIG)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_backtest(config: dict):
    """Use the same validated research service as the API."""
    from src.api import BacktestRunRequest
    from src.api.backtest_service import run_backtest_sync

    params = {key: value for key, value in config["strategy"].items() if key != "name"}
    request = BacktestRunRequest(strategy_name=config["strategy"]["name"], strategy_params=params, **config["backtest"])
    result = run_backtest_sync(request)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    if not result.get("success"):
        raise RuntimeError(result.get("error", "Backtest failed"))
    return result


def run_live_trading(config: dict):
    """运行实盘交易"""
    logger.info("=" * 60)
    logger.info("开始实盘交易")
    logger.info("=" * 60)

    trading_config = dict(config.get("trading", {}))
    if "risk" in config:
        trading_config["risk"] = config["risk"]
    gateway_type = trading_config.get("gateway", "vnpy")
    logger.info(f"使用交易网关: {gateway_type}")

    gateway = create_gateway(gateway_type)
    trading_engine = TradingEngine(gateway)

    strategy = create_strategy(config["strategy"]["name"], {k: v for k, v in config["strategy"].items() if k != "name"})
    strategy.initial_capital = trading_config.get("initial_capital", 1000000)
    trading_engine.set_strategy(strategy)

    from src.runtime import InstanceLock

    instance = InstanceLock(os.getenv("QUANT_INSTANCE_LOCK", "data/runtime/executor.lock"))
    instance.acquire()
    import atexit

    atexit.register(trading_engine.close)
    atexit.register(instance.release)
    success = trading_engine.start(trading_config)
    if not success:
        logger.error("实盘交易启动失败")
        return

    logger.info("实盘交易启动成功，按 Ctrl+C 停止")

    try:
        while True:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        pass

    trading_engine.close()


def main():
    configure_logging()

    parser = argparse.ArgumentParser(description="量化交易系统")
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--mode", "-m", choices=["backtest", "live"], help="运行模式")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.mode:
        config["mode"] = args.mode

    mode = config.get("mode", "backtest")
    if mode == "backtest":
        run_backtest(config)
    elif mode == "live":
        run_live_trading(config)
    else:
        logger.error(f"未知模式: {mode}")


if __name__ == "__main__":
    main()
