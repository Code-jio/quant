#!/usr/bin/env python3
"""
测试TqSdk连接SimNow的脚本
"""

import logging
from tqsdk import TqApi, TqAuth, TqAccount, TqSim

# 设置日志级别为DEBUG以查看详细连接信息
logging.basicConfig(level=logging.DEBUG)

def test_simnow_connection(username, password, broker_id='9999'):
    """
    测试SimNow连接
    """
    print(f"尝试连接SimNow，用户ID: {username}")

    try:
        # 使用正确的SimNow连接方式
        account = TqAccount(broker_id, username, password)
        api = TqApi(account, auth=TqAuth("simnow_client_test", "0000000000000000"))

        print("正在等待连接...")
        api.wait_update()
        print("连接成功!")

        # 查询账户信息以验证连接
        account_info = api.get_account()
        print(f"账户余额: {account_info.balance}")

        api.close()
        return True

    except Exception as e:
        print(f"连接失败: {e}")
        return False

if __name__ == "__main__":
    # 使用你配置文件中的账户信息
    username = "0061840190"
    password = "025419"
    broker_id = "9999"

    print("开始测试SimNow连接...")
    success = test_simnow_connection(username, password, broker_id)

    if success:
        print("连接测试成功！")
    else:
        print("连接测试失败。")