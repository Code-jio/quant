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


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/config_production.json"

DEFAULT_CONFIG = {
    "mode": "backtest",
    "backtest": {
        "start_date": "2023-01-01",
        "end_date": "2024-12-31",
        "initial_capital": 1000000,
        "commission_rate": 0.0003,
        "slip_rate": 0.0001,
        "margin_rate": 0.12
    },
    "strategy": {
        "name": "ma_cross",
        "symbol": "IF9999",
        "fast_period": 10,
        "slow_period": 20,
        "position_ratio": 0.8
    },
    "trading": {
        "gateway": "vnpy",
        "username": "",
        "password": "",
        "broker_id": "2071",
        "td_server": "tcp://114.94.128.1:42205",
        "md_server": "tcp://114.94.128.1:42213",
        "app_id": "client_TraderMaster_v1.0.0",
        "auth_code": "20260324LHJYMHBG",
        "vnpy_environment": "实盘",
        "initial_capital": 1000000
    },
    "risk": {
        "enabled": True,
        "max_order_volume": 1000,
        "max_position_volume": 10000,
        "max_active_orders": 200,
        "max_orders_per_minute": 120,
        "max_daily_loss_ratio": 0.10,
        "allow_market_orders": True,
        "allowed_symbols": [],
        "blocked_symbols": []
    }
}


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """加载配置文件，不存在时使用默认配置"""
    if not os.path.exists(config_path):
        logger.warning(f"配置文件不存在: {config_path}，使用内置默认配置")
        return DEFAULT_CONFIG.copy()
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def run_backtest(config: dict):
    """运行回测"""
    logger.info("=" * 60)
    logger.info("开始回测")
    logger.info("=" * 60)

    bt_config = BacktestConfig(
        start_date=config['backtest']['start_date'],
        end_date=config['backtest']['end_date'],
        initial_capital=config['backtest']['initial_capital'],
        commission_rate=config['backtest']['commission_rate'],
        slip_rate=config['backtest']['slip_rate'],
        margin_rate=config['backtest']['margin_rate']
    )

    data_manager = DataManager()
    symbol = config['strategy']['symbol']
    logger.info(f"生成/加载 {symbol} 模拟数据...")
    data_manager.generate_sample_data(symbol, days=500)

    strategy = create_strategy(config['strategy']['name'], config['strategy'])

    engine = BacktestEngine(bt_config)
    engine.set_data_manager(data_manager)
    engine.set_strategy(strategy)

    result = engine.run()

    logger.info("\n" + "=" * 60)
    logger.info("回测结果")
    logger.info("=" * 60)
    logger.info(f"总收益率:   {result.total_return:.2%}")
    logger.info(f"年化收益率: {result.annual_return:.2%}")
    logger.info(f"夏普比率:   {result.sharpe_ratio:.2f}")
    logger.info(f"最大回撤:   {result.max_drawdown_pct:.2%}")
    logger.info(f"胜率:      {result.win_rate:.2%}")
    logger.info(f"总交易次数: {result.total_trades}")

    analyzer = Analyzer(initial_capital=bt_config.initial_capital)
    analyzer.set_data(list(engine.equity_curve.values()), result.trades)
    print(analyzer.generate_report())

    return result


def run_live_trading(config: dict):
    """运行实盘交易"""
    logger.info("=" * 60)
    logger.info("开始实盘交易")
    logger.info("=" * 60)

    trading_config = dict(config.get('trading', {}))
    if 'risk' in config:
        trading_config['risk'] = config['risk']
    gateway_type = trading_config.get('gateway', 'vnpy')
    logger.info(f"使用交易网关: {gateway_type}")

    gateway = create_gateway(gateway_type)
    trading_engine = TradingEngine(gateway)

    strategy = create_strategy(config['strategy']['name'], config['strategy'])
    strategy.initial_capital = trading_config.get('initial_capital', 1000000)
    trading_engine.set_strategy(strategy)

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

    trading_engine.stop()


def main():
    parser = argparse.ArgumentParser(description='量化交易系统')
    parser.add_argument('--config', '-c', default=DEFAULT_CONFIG_PATH, help='配置文件路径')
    parser.add_argument('--mode', '-m', choices=['backtest', 'live'], help='运行模式')
    args = parser.parse_args()

    config = load_config(args.config)
    if args.mode:
        config['mode'] = args.mode

    mode = config.get('mode', 'backtest')
    if mode == 'backtest':
        run_backtest(config)
    elif mode == 'live':
        run_live_trading(config)
    else:
        logger.error(f"未知模式: {mode}")


if __name__ == "__main__":
    main()
