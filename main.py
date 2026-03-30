"""
量化交易系统主入口
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data import DataManager
from src.strategy import create_strategy, STRATEGY_REGISTRY
from src.backtest import BacktestEngine, BacktestConfig
from src.trading import TradingEngine, create_gateway
from src.analysis import Analyzer


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config/config_simnow.json") -> dict:
    """加载配置文件"""
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
    df = data_manager.generate_sample_data(symbol, days=500)
    
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
    logger.info(f"盈利次数:   {result.winning_trades}")
    logger.info(f"亏损次数:   {result.losing_trades}")
    
    analyzer = Analyzer(initial_capital=bt_config.initial_capital)
    analyzer.set_data(engine.equity_curve.values(), result.trades)
    report = analyzer.generate_report()
    print(report)
    
    return result


def run_live_trading(config: dict):
    """运行实盘交易"""
    logger.info("=" * 60)
    logger.info("开始实盘交易")
    logger.info("=" * 60)

    trading_config = config.get('trading', {})
    gateway_type = trading_config.get('gateway', 'simulated')

    logger.info(f"使用交易网关: {gateway_type}")

    # 根据配置创建网关
    gateway = create_gateway(gateway_type)
    trading_engine = TradingEngine(gateway)

    strategy = create_strategy(config['strategy']['name'], config['strategy'])
    strategy.initial_capital = trading_config.get('initial_capital', 1000000)
    trading_engine.set_strategy(strategy)

    success = trading_engine.start(trading_config)

    if success:
        logger.info("实盘交易启动成功")

        # 如果是模拟网关，启动行情模拟
        if gateway_type == 'simulated':
            symbols = [config['strategy']['symbol']]
            base_prices = {symbols[0]: 4000.0}
            gateway.start_quote_simulation(symbols, base_prices)
        # 如果是 TqSdk 网关，订阅行情
        elif gateway_type == 'tqsdk':
            symbols = [config['strategy']['symbol']]
            gateway.subscribe_market_data(symbols)

        try:
            # TqSdk 需要 wait_update 来接收行情
            if gateway_type == 'tqsdk':
                logger.info("TqSdk 网关运行中，按 Ctrl+C 停止...")
                while True:
                    gateway.wait_update()

                    # 获取并更新行情
                    for symbol in [config['strategy']['symbol']]:
                        tick = gateway.get_quote(symbol)
                        if tick:
                            strategy.on_bar({
                                'symbol': symbol,
                                'datetime': tick.timestamp,
                                'open': tick.last_price,
                                'high': tick.last_price,
                                'low': tick.last_price,
                                'close': tick.last_price,
                                'volume': tick.volume
                            })
            else:
                while True:
                    input("按 Enter 停止交易...")
                    break
        except KeyboardInterrupt:
            pass

        trading_engine.stop()
    else:
        logger.error("实盘交易启动失败")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='量化交易系统')
    parser.add_argument('--config', '-c', default='config/config_ctp_plus_simnow.json', help='配置文件路径')
    parser.add_argument('--mode', '-m', choices=['backtest', 'live'], help='运行模式（覆盖配置文件中的设置）')
    args = parser.parse_args()

    config_path = args.config

    if not os.path.exists(config_path):
        logger.warning(f"配置文件不存在: {config_path}, 使用默认配置")
        config = {
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
                "gateway": "simulated",
                "initial_capital": 1000000
            }
        }
    else:
        config = load_config(config_path)

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
