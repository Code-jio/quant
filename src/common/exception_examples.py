"""
异常处理模块使用示例
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.exceptions import ExceptionHandler, retry, timeout, handle_errors
import time


def example_usage():
    """异常处理功能使用示例"""
    print("=== 异常处理功能使用示例 ===\n")

    # 创建异常处理器
    handler = ExceptionHandler(log_file="logs/error_log.txt")
    error_handler_instance = handler.error_handler  # 获取内部的ErrorHandler实例

    # 1. 使用 @retry 装饰器
    print("1. 使用重试装饰器:")

    attempt_count = 0

    @retry(max_retries=3, initial_delay=0.5, error_handler=error_handler_instance)
    def unstable_function():
        nonlocal attempt_count
        attempt_count += 1
        print(f"  尝试 #{attempt_count}")
        if attempt_count < 3:
            raise ConnectionError(f"连接失败 (尝试 {attempt_count})")
        return "成功!"

    try:
        result = unstable_function()
        print(f"  结果: {result}\n")
    except Exception as e:
        print(f"  最终失败: {e}\n")

    # 2. 使用 @timeout 装饰器
    print("2. 使用超时装饰器:")

    @timeout(seconds=2.0, error_handler=error_handler_instance)
    def slow_function():
        print("  开始执行耗时操作...")
        time.sleep(3)  # 模拟耗时操作
        print("  操作完成")
        return "完成"

    try:
        result = slow_function()
        print(f"  结果: {result}\n")
    except TimeoutError as e:
        print(f"  操作超时: {e}\n")

    # 3. 使用 @handle_errors 装饰器
    print("3. 使用异常捕获装饰器:")

    @handle_errors(default_return="默认值", error_handler=error_handler_instance)
    def risky_function(should_fail=True):
        if should_fail:
            raise ValueError("故意抛出的异常")
        return "成功"

    result = risky_function()
    print(f"  结果: {result}\n")

    # 4. 使用 ExceptionHandler 的高级功能
    print("4. 使用 ExceptionHandler 高级功能:")

    # 模拟网络请求
    def network_request():
        raise ConnectionError("网络请求失败")

    result = handler.handle_network_request(network_request, max_retries=2)
    print(f"  网络请求结果: {result}")

    # 模拟数据库操作
    def db_operation():
        raise Exception("数据库操作失败")

    result = handler.handle_database_operation(db_operation, max_retries=2)
    print(f"  数据库操作结果: {result}")

    # 模拟交易操作
    def trade_operation():
        raise Exception("交易操作失败")

    result = handler.handle_trading_operation(trade_operation, max_retries=1, timeout=5.0)
    print(f"  交易操作结果: {result}")

    # 5. 获取错误报告
    print("\n5. 错误统计报告:")
    stats = handler.get_error_summary()
    print(f"  总错误数: {stats['total_errors']}")
    print(f"  按类型分布: {stats['by_type']}")

    print("\n6. 详细错误报告:")
    report = handler.generate_error_report(hours=1)
    print(f"  报告周期: 最近{report['period_hours']}小时")
    print(f"  总错误数: {report['total_errors']}")
    print(f"  操作类型: {report['unique_operations']}")
    print(f"  错误类型分布: {report['error_types_distribution']}")


if __name__ == "__main__":
    example_usage()