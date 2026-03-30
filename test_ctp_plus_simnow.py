"""
CtpPlus SimNow 连接测试脚本
用于测试 CtpPlus 网关与 SimNow 模拟环境的连接
"""

import sys
import os
import time
import json
import logging
from datetime import datetime

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.trading import create_gateway
from src.strategy import create_strategy

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_ctp_plus_connection():
    """测试 CtpPlus 连接"""
    logger.info("=" * 60)
    logger.info("开始测试 CtpPlus SimNow 连接")
    logger.info("=" * 60)

    # 加载配置
    config_path = "config/config_ctp_plus_simnow.json"
    if not os.path.exists(config_path):
        logger.error(f"配置文件不存在: {config_path}")
        return False

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 提取交易配置
    trading_config = config.get('trading', {})

    # 检查是否提供了必要的账户信息
    username = trading_config.get('username', '')
    password = trading_config.get('password', '')

    if not username or not password or username == "YOUR_SIMNOW_ACCOUNT" or password == "YOUR_SIMNOW_PASSWORD":
        logger.error("请先在配置文件中填写正确的SimNow账号和密码！")
        logger.info(f"请编辑 {config_path} 文件，替换YOUR_SIMNOW_ACCOUNT和YOUR_SIMNOW_PASSWORD")
        return False

    try:
        # 创建CtpPlus网关
        logger.info("创建 CtpPlus 网关...")
        gateway = create_gateway('ctpplus')

        # 设置回调函数
        def on_order_callback(order):
            logger.info(f"收到订单回调: {order.order_id}, 状态: {order.status.value}")

        def on_trade_callback(trade):
            logger.info(f"收到成交回调: {trade.trade_id}, 价格: {trade.price}")

        def on_position_callback(position):
            logger.info(f"收到持仓回调: {position.symbol}, 方向: {position.direction.value}, 数量: {position.volume}")

        def on_account_callback(account):
            logger.info(f"收到账户回调: 余额={account.balance}, 可用={account.available}")

        def on_tick_callback(tick):
            logger.info(f"收到行情: {tick.symbol}, 价格={tick.last_price}")

        def on_error_callback(error, context):
            logger.error(f"收到错误回调: {context} - {error}")

        # 注册回调
        gateway.on_order_callback = on_order_callback
        gateway.on_trade_callback = on_trade_callback
        gateway.on_position_callback = on_position_callback
        gateway.on_account_callback = on_account_callback
        gateway.on_tick_callback = on_tick_callback
        gateway.on_error_callback = on_error_callback

        # 连接
        logger.info("正在连接到 SimNow 模拟环境...")
        success = gateway.connect(trading_config)

        if not success:
            logger.error("连接失败")
            return False

        logger.info("连接成功！")

        # 等待一段时间以确保连接稳定
        time.sleep(3)

        # 测试查询账户信息
        logger.info("测试查询账户信息...")
        account = gateway.query_account()
        if account:
            logger.info(f"账户信息: 余额={account.balance}, 可用={account.available}, 保证金={account.margin}")
        else:
            logger.warning("未能获取账户信息")

        # 测试查询持仓
        logger.info("测试查询持仓...")
        positions = gateway.query_positions()
        logger.info(f"持仓数量: {len(positions)}")

        # 测试查询订单
        logger.info("测试查询订单...")
        orders = gateway.query_orders()
        logger.info(f"订单数量: {len(orders)}")

        # 订阅行情测试
        symbol = config['strategy']['symbol']
        logger.info(f"订阅合约 {symbol} 行情...")
        gateway.subscribe_market_data([symbol])

        # 等待几秒钟接收行情
        logger.info("等待行情数据...")
        time.sleep(5)

        # 如果需要测试下单功能，取消下面的注释（需谨慎！）
        # 测试发送订单
        # logger.info("准备发送测试订单...")
        # from src.strategy import Signal, Direction, OrderType
        # signal = Signal(
        #     symbol=symbol,
        #     direction=Direction.LONG,
        #     price=3800.0,
        #     volume=1,
        #     order_type=OrderType.LIMIT
        # )
        # order_id = gateway.send_order(signal)
        # if order_id:
        #     logger.info(f"订单已发送: {order_id}")
        # else:
        #     logger.error("订单发送失败")

        # 等待一段时间以观察回调
        time.sleep(2)

        logger.info("测试完成，准备断开连接...")

        # 断开连接
        gateway.disconnect()

        logger.info("CtpPlus SimNow 连接测试完成")
        return True

    except Exception as e:
        logger.error(f"CtpPlus 连接测试异常: {e}")
        logger.exception(e)
        return False

if __name__ == "__main__":
    success = test_ctp_plus_connection()
    if success:
        logger.info("✅ CtpPlus SimNow 连接测试成功")
    else:
        logger.error("❌ CtpPlus SimNow 连接测试失败")