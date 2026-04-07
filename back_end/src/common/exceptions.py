"""
异常处理模块
提供重试、超时、熔断、错误汇报等异常处理功能
"""

import logging
import time
import functools
import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, Type, Any
from dataclasses import dataclass
from collections import deque
import traceback
import threading

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """错误类型"""
    NETWORK = "network"           # 网络错误
    TIMEOUT = "timeout"           # 超时错误
    AUTH = "auth"               # 认证错误
    BUSINESS = "business"         # 业务错误
    SYSTEM = "system"           # 系统错误
    UNKNOWN = "unknown"          # 未知错误


@dataclass
class ErrorInfo:
    """错误信息"""
    error_type: ErrorType
    message: str
    exception: Optional[Exception] = None
    traceback: Optional[str] = None
    timestamp: datetime = None
    retry_count: int = 0
    operation: str = ""
    details: dict = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.details is None:
            self.details = {}

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "error_type": self.error_type.value,
            "message": self.message,
            "exception": str(self.exception),
            "traceback": self.traceback,
            "timestamp": self.timestamp.isoformat(),
            "retry_count": self.retry_count,
            "operation": self.operation,
            "details": self.details
        }


class CircuitBreaker:
    """熔断器

    当连续失败次数达到阈值时，暂时停止请求，防止雪崩效应
    """

    def __init__(self, failure_threshold: int = 5, timeout: float = 60.0):
        """
        初始化熔断器

        Args:
            failure_threshold: 失败阈值，超过此值触发熔断
            timeout: 熔断超时时间（秒），超时后恢复
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "closed"  # closed: 正常, open: 熔断, half-open: 半开
        self._lock = threading.Lock()

    def record_failure(self):
        """记录失败"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()

            if self.failure_count >= self.failure_threshold:
                self.state = "open"
                logger.warning(f"熔断器已打开 (失败次数: {self.failure_count})")

    def record_success(self):
        """记录成功"""
        with self._lock:
            self.failure_count = 0
            if self.state == "half-open":
                self.state = "closed"
                logger.info("熔断器已恢复")

    def can_request(self) -> bool:
        """检查是否可以发送请求"""
        with self._lock:
            if self.state == "closed":
                return True

            if self.state == "open":
                # 检查是否超时恢复
                if self.last_failure_time:
                    elapsed = (datetime.now() - self.last_failure_time).total_seconds()
                    if elapsed >= self.timeout:
                        self.state = "half-open"
                        logger.info("熔断器进入半开状态")
                        return True
                return False

            return self.state != "open"

    def reset(self):
        """重置熔断器"""
        with self._lock:
            self.failure_count = 0
            self.state = "closed"
            self.last_failure_time = None


class RetryPolicy:
    """重试策略"""

    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 10.0,
        backoff_factor: float = 2.0,
        retry_on: tuple = (Exception,),
        circuit_breaker: Optional[CircuitBreaker] = None
    ):
        """
        初始化重试策略

        Args:
            max_retries: 最大重试次数
            initial_delay: 初始延迟时间（秒）
            max_delay: 最大延迟时间（秒）
            backoff_factor: 退避因子，每次重试延迟时间乘以此因子
            retry_on: 需要重试的异常类型
            circuit_breaker: 熔断器
        """
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor
        self.retry_on = retry_on
        self.circuit_breaker = circuit_breaker


class ErrorReporter:
    """错误汇报器

    记录和分析错误信息
    """

    def __init__(self, log_file: Optional[str] = None, notification_callback: Optional[Callable] = None):
        """
        初始化错误汇报器

        Args:
            log_file: 错误日志文件路径
            notification_callback: 错误通知回调函数，接收ErrorInfo作为参数
        """
        self.error_history: deque = deque(maxlen=1000)  # 保留最近1000条错误
        self.error_stats: dict = {}
        self.log_file = log_file
        self.notification_callback = notification_callback
        self.error_thresholds = {  # 错误阈值配置
            ErrorType.NETWORK: 5,
            ErrorType.TIMEOUT: 5,
            ErrorType.AUTH: 3,
            ErrorType.BUSINESS: 10,
            ErrorType.SYSTEM: 3,
            ErrorType.UNKNOWN: 5
        }
        self.threshold_counts = {error_type: 0 for error_type in ErrorType}

    def report(self, error_info: ErrorInfo):
        """汇报错误"""
        # 记录到历史
        self.error_history.append(error_info)

        # 更新统计
        error_type = error_info.error_type.value
        if error_type not in self.error_stats:
            self.error_stats[error_type] = {
                "count": 0,
                "last_time": None,
                "first_time": error_info.timestamp
            }
        self.error_stats[error_type]["count"] += 1
        self.error_stats[error_type]["last_time"] = error_info.timestamp

        # 更新阈值计数
        self.threshold_counts[error_info.error_type] += 1

        # 写入日志文件
        if self.log_file:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{error_info.timestamp}] {error_info.to_dict()}\n")
            except Exception as e:
                logger.error(f"写入错误日志文件失败: {e}")

        # 记录到日志
        logger.error(f"{error_info.operation} - {error_info.message}")

        if error_info.traceback:
            logger.debug(f"错误堆栈:\n{error_info.traceback}")

        # 检查是否超过阈值并发送通知
        if self.threshold_counts[error_info.error_type] >= self.error_thresholds[error_info.error_type]:
            self._send_alert(error_info)

        # 执行通知回调
        if self.notification_callback:
            try:
                self.notification_callback(error_info)
            except Exception as e:
                logger.error(f"执行错误通知回调失败: {e}")

    def get_stats(self) -> dict:
        """获取错误统计"""
        return {
            "total_errors": len(self.error_history),
            "by_type": self.error_stats,
            "threshold_counts": {k.value: v for k, v in self.threshold_counts.items()},
            "threshold_exceeded": {
                k.value: v >= self.error_thresholds[k]
                for k, v in self.threshold_counts.items()
            },
            "recent_errors": [
                e.to_dict() for e in list(self.error_history)[-10:]
            ]
        }

    def get_report(self, hours: int = 24) -> dict:
        """生成错误报告

        Args:
            hours: 统计最近多少小时内的错误

        Returns:
            错误报告字典
        """
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_errors = [
            e for e in self.error_history
            if e.timestamp >= cutoff_time
        ]

        report = {
            "period_hours": hours,
            "report_time": datetime.now().isoformat(),
            "total_errors": len(recent_errors),
            "unique_operations": list(set(e.operation for e in recent_errors)),
            "error_types_distribution": {},
            "top_operations_by_error": {},
            "error_timeline": [],
            "summary": {
                "critical_errors": 0,  # 高频错误
                "unique_errors": len(set(e.message for e in recent_errors)),
                "avg_errors_per_hour": len(recent_errors) / hours if hours > 0 else 0
            }
        }

        # 按错误类型统计
        type_counts = {}
        for error in recent_errors:
            error_type = error.error_type.value
            type_counts[error_type] = type_counts.get(error_type, 0) + 1

        report["error_types_distribution"] = type_counts

        # 按操作统计
        op_counts = {}
        for error in recent_errors:
            op = error.operation
            op_counts[op] = op_counts.get(op, 0) + 1

        report["top_operations_by_error"] = dict(sorted(op_counts.items(), key=lambda x: x[1], reverse=True)[:10])

        # 时间线
        for error in recent_errors[-50:]:  # 最近50个错误
            report["error_timeline"].append({
                "timestamp": error.timestamp.isoformat(),
                "operation": error.operation,
                "error_type": error.error_type.value,
                "message": error.message[:100]  # 截断长消息
            })

        # 总结关键指标
        report["summary"]["critical_errors"] = sum(1 for count in type_counts.values() if count > 10)

        return report

    def clear(self):
        """清除错误记录"""
        self.error_history.clear()
        self.error_stats.clear()
        self.threshold_counts = {error_type: 0 for error_type in ErrorType}

    def _send_alert(self, error_info: ErrorInfo):
        """发送警报"""
        alert_msg = f"严重错误警告: {error_info.error_type.value} 类型错误超过阈值 " \
                   f"({self.threshold_counts[error_info.error_type]}/{self.error_thresholds[error_info.error_type]}) " \
                   f"- 操作: {error_info.operation} - {error_info.message}"

        logger.critical(alert_msg)

        # 重置阈值计数
        self.threshold_counts[error_info.error_type] = 0

    def export_to_json(self, filepath: str, hours: int = 24) -> bool:
        """导出错误报告到JSON文件"""
        try:
            report = self.get_report(hours)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"错误报告已导出到: {filepath}")
            return True
        except Exception as e:
            logger.error(f"导出错误报告失败: {e}")
            return False


class ErrorHandler:
    """错误处理器

    整合重试、熔断、错误汇报等功能
    """

    def __init__(self, log_file: Optional[str] = None, enable_circuit_breaker: bool = True):
        """
        初始化错误处理器

        Args:
            log_file: 错误日志文件路径
            enable_circuit_breaker: 是否启用熔断器
        """
        self.reporter = ErrorReporter(log_file)
        self.circuit_breaker = CircuitBreaker() if enable_circuit_breaker else None

    def classify_error(self, exception: Exception) -> ErrorType:
        """分类异常类型"""
        error_name = type(exception).__name__.lower()

        if "timeout" in error_name or "timedout" in error_name:
            return ErrorType.TIMEOUT
        elif "connection" in error_name or "network" in error_name:
            return ErrorType.NETWORK
        elif "auth" in error_name or "login" in error_name or "permission" in error_name:
            return ErrorType.AUTH
        elif "value" in error_name or "key" in error_name:
            return ErrorType.BUSINESS
        else:
            return ErrorType.UNKNOWN

    def handle_error(
        self,
        operation: str,
        exception: Exception,
        retry_count: int = 0,
        details: dict = None
    ):
        """处理错误"""
        error_type = self.classify_error(exception)

        error_info = ErrorInfo(
            error_type=error_type,
            message=str(exception),
            exception=exception,
            traceback=traceback.format_exc(),
            operation=operation,
            retry_count=retry_count,
            details=details or {}
        )

        # 记录错误
        self.reporter.report(error_info)

        # 记录到熔断器
        if self.circuit_breaker:
            self.circuit_breaker.record_failure()

        return error_info


def retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    retry_on: tuple = (Exception,),
    on_retry: Optional[Callable] = None,
    error_handler: Optional[ErrorHandler] = None,
    condition: Optional[Callable[[Exception], bool]] = None
):
    """
    重试装饰器

    Args:
        max_retries: 最大重试次数
        initial_delay: 初始延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        backoff_factor: 退避因子
        retry_on: 需要重试的异常类型
        on_retry: 重试时的回调函数
        error_handler: 错误处理器
        condition: 自定义重试条件函数，接收异常作为参数，返回是否重试

    Returns:
        装饰器函数

    Example:
        @retry(max_retries=3, initial_delay=1.0)
        def send_order(symbol, direction, volume):
            # 可能失败的操作
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 检查熔断器
            if error_handler and error_handler.circuit_breaker:
                if not error_handler.circuit_breaker.can_request():
                    raise Exception("熔断器已打开，暂停请求")

            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except Exception as e:
                    last_exception = e

                    # 检查是否需要重试
                    should_retry = isinstance(e, retry_on)

                    # 如果提供了自定义条件，则同时满足条件才重试
                    if condition and should_retry:
                        should_retry = condition(e)

                    if not should_retry:
                        raise

                    # 如果是最后一次尝试，直接抛出异常
                    if attempt == max_retries:
                        break

                    # 处理错误
                    if error_handler:
                        error_handler.handle_error(
                            operation=func.__name__,
                            exception=e,
                            retry_count=attempt
                        )

                    # 执行重试回调
                    if on_retry:
                        on_retry(attempt + 1, e)

                    # 计算延迟时间
                    delay = min(initial_delay * (backoff_factor ** attempt), max_delay)
                    logger.warning(f"{func.__name__} 失败，{delay}秒后重试 ({attempt + 1}/{max_retries})")
                    time.sleep(delay)

            # 所有重试都失败
            raise last_exception

        return wrapper
    return decorator


def timeout(seconds: float, error_handler: Optional[ErrorHandler] = None):
    """
    超时装饰器 - 在Windows和其他平台上都能正常工作的版本

    Args:
        seconds: 超时时间（秒）
        error_handler: 错误处理器

    Returns:
        装饰器函数

    Example:
        @timeout(seconds=5.0)
        def slow_operation():
            time.sleep(10)  # 这会超时
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 在Windows上使用threading实现超时，因为signal不能很好地处理多线程
            import threading

            result = [None]
            exception = [None]
            finished = threading.Event()

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e
                finally:
                    finished.set()

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            thread.join(timeout=seconds)

            if not finished.is_set():
                # 超时
                timeout_error = TimeoutError(f"{func.__name__} 执行超时 ({seconds}秒)")
                if error_handler:
                    error_handler.handle_error(
                        operation=func.__name__,
                        exception=timeout_error
                    )
                raise timeout_error

            if exception[0]:
                raise exception[0]

            return result[0]

        return wrapper
    return decorator


def handle_errors(
    default_return: Any = None,
    log_exception: bool = True,
    error_handler: Optional[ErrorHandler] = None
):
    """
    异常捕获装饰器

    Args:
        default_return: 发生异常时的默认返回值
        log_exception: 是否记录异常
        error_handler: 错误处理器

    Returns:
        装饰器函数

    Example:
        @handle_errors(default_return=False)
        def might_fail():
            # 可能失败的操作
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_exception:
                    logger.error(f"{func.__name__} 发生异常: {e}")

                if error_handler:
                    error_handler.handle_error(
                        operation=func.__name__,
                        exception=e
                    )

                return default_return

        return wrapper
    return decorator


class ExceptionHandler:
    """异常处理工具类 - 提供常用的异常处理方法"""

    def __init__(self, log_file: Optional[str] = None, enable_circuit_breaker: bool = True):
        self.error_handler = ErrorHandler(log_file, enable_circuit_breaker)

    def handle_network_request(self,
                              request_func: Callable,
                              max_retries: int = 3,
                              timeout: float = 10.0,
                              retry_conditions: Optional[dict] = None) -> Any:
        """
        处理网络请求，包含重试和超时逻辑

        Args:
            request_func: 网络请求函数
            max_retries: 最大重试次数
            timeout: 单次请求超时时间
            retry_conditions: 重试条件配置

        Returns:
            请求结果或None
        """
        # 创建包装函数以应用多重装饰器
        def wrapped_func():
            # 先应用重试逻辑
            @retry(
                max_retries=max_retries,
                initial_delay=1.0,
                backoff_factor=2.0,
                retry_on=(ConnectionError, TimeoutError, IOError),
                error_handler=self.error_handler,
                condition=lambda e: retry_conditions.get(type(e).__name__, True) if retry_conditions else True
            )
            def retry_wrapper():
                return request_func()

            # 再应用超时逻辑
            @timeout(seconds=timeout, error_handler=self.error_handler)
            def timeout_wrapper():
                return retry_wrapper()

            return timeout_wrapper()

        try:
            return wrapped_func()
        except Exception as e:
            logger.error(f"网络请求最终失败: {e}")
            return None

    def handle_database_operation(self,
                                 db_func: Callable,
                                 max_retries: int = 3) -> Any:
        """
        处理数据库操作，包含重试逻辑

        Args:
            db_func: 数据库操作函数
            max_retries: 最大重试次数

        Returns:
            操作结果或None
        """
        @retry(
            max_retries=max_retries,
            initial_delay=0.5,
            backoff_factor=1.5,
            retry_on=(Exception,),  # 捕获所有数据库异常
            error_handler=self.error_handler
        )
        def db_operation_with_retry():
            return db_func()

        try:
            return db_operation_with_retry()
        except Exception as e:
            logger.error(f"数据库操作最终失败: {e}")
            return None

    def handle_trading_operation(self,
                                trade_func: Callable,
                                max_retries: int = 2,
                                timeout: float = 5.0) -> Any:
        """
        处理交易操作，包含重试和超时逻辑

        Args:
            trade_func: 交易操作函数
            max_retries: 最大重试次数
            timeout: 操作超时时间

        Returns:
            操作结果或None
        """
        # 创建包装函数以应用多重装饰器
        def wrapped_func():
            @retry(
                max_retries=max_retries,
                initial_delay=0.5,
                backoff_factor=2.0,
                retry_on=(Exception,),
                error_handler=self.error_handler
            )
            def retry_wrapper():
                return trade_func()

            @timeout(seconds=timeout, error_handler=self.error_handler)
            def timeout_wrapper():
                return retry_wrapper()

            return timeout_wrapper()

        try:
            return wrapped_func()
        except Exception as e:
            logger.error(f"交易操作最终失败: {e}")
            return None

    def get_error_summary(self) -> dict:
        """获取错误摘要"""
        return self.error_handler.reporter.get_stats()

    def generate_error_report(self, hours: int = 24) -> dict:
        """生成错误报告"""
        return self.error_handler.reporter.get_report(hours)
