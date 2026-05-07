"""
异常处理模块测试用例
"""
import time
import threading
from unittest.mock import Mock
from .exceptions import (
    ErrorHandler, ErrorType, ErrorInfo,
    retry, timeout, handle_errors,
    ExceptionHandler
)


def test_basic_error_handling():
    """测试基本错误处理功能"""
    print("=== 测试基本错误处理功能 ===")

    handler = ErrorHandler()

    # 模拟一个异常
    try:
        raise ValueError("这是一个测试错误")
    except ValueError as e:
        error_info = handler.handle_error("test_operation", e)
        print(f"错误已处理: {error_info.message}")
        print(f"错误类型: {error_info.error_type}")

    stats = handler.reporter.get_stats()
    print(f"错误统计: {stats}")


def test_retry_mechanism():
    """测试重试机制"""
    print("\n=== 测试重试机制 ===")

    handler = ErrorHandler()
    attempts = 0

    @retry(max_retries=3, initial_delay=0.1, error_handler=handler)
    def flaky_function():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError(f"第{attempts}次尝试失败")
        return "成功"

    try:
        result = flaky_function()
        print(f"函数执行成功: {result}, 总尝试次数: {attempts}")
    except Exception as e:
        print(f"函数执行失败: {e}")

    stats = handler.reporter.get_stats()
    print(f"错误统计: {stats}")


def test_timeout_functionality():
    """测试超时功能"""
    print("\n=== 测试超时功能 ===")

    handler = ErrorHandler()

    @timeout(seconds=0.5, error_handler=handler)
    def slow_function():
        time.sleep(1)  # 故意超过超时时间
        return "完成"

    try:
        result = slow_function()
        print(f"函数执行成功: {result}")
    except TimeoutError as e:
        print(f"函数执行超时: {e}")

    stats = handler.reporter.get_stats()
    print(f"错误统计: {stats}")


def test_exception_handler_class():
    """测试ExceptionHandler类"""
    print("\n=== 测试ExceptionHandler类 ===")

    handler = ExceptionHandler()

    # 测试网络请求处理
    def mock_network_request():
        raise ConnectionError("网络连接失败")

    try:
        result = handler.handle_network_request(mock_network_request, max_retries=2)
        print(f"网络请求结果: {result}")
    except Exception as e:
        print(f"网络请求处理异常: {e}")

    # 测试数据库操作处理
    def mock_db_operation():
        raise Exception("数据库操作失败")

    try:
        result = handler.handle_database_operation(mock_db_operation, max_retries=2)
        print(f"数据库操作结果: {result}")
    except Exception as e:
        print(f"数据库操作处理异常: {e}")

    # 测试交易操作处理
    def mock_trade_operation():
        raise Exception("交易操作失败")

    try:
        result = handler.handle_trading_operation(mock_trade_operation, max_retries=1, timeout=2.0)
        print(f"交易操作结果: {result}")
    except Exception as e:
        print(f"交易操作处理异常: {e}")

    # 输出错误报告
    report = handler.generate_error_report(hours=1)
    print(f"错误报告: {report}")


def test_circuit_breaker():
    """测试熔断器功能"""
    print("\n=== 测试熔断器功能 ===")

    handler = ErrorHandler()

    # 模拟连续失败以触发熔断
    for i in range(6):  # 默认阈值是5，第6次会触发熔断
        try:
            raise Exception(f"模拟失败 #{i+1}")
        except Exception as e:
            handler.handle_error(f"operation_{i}", e)

    # 现在尝试调用应该被熔断
    if handler.circuit_breaker and not handler.circuit_breaker.can_request():
        print("熔断器已打开，请求被阻止")
    else:
        print("熔断器未打开")

    # 等待一段时间后，熔断器应该恢复
    time.sleep(1)
    if handler.circuit_breaker and handler.circuit_breaker.can_request():
        print("熔断器已恢复")


def main():
    """运行所有测试"""
    print("开始测试异常处理模块...\n")

    test_basic_error_handling()
    test_retry_mechanism()
    test_timeout_functionality()
    test_exception_handler_class()
    test_circuit_breaker()

    print("\n异常处理模块测试完成!")


if __name__ == "__main__":
    main()