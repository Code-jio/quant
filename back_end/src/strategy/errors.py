"""
策略模块异常定义
"""


class SignalError(Exception):
    """信号生成错误"""
    pass


class OrderError(Exception):
    """订单错误"""
    pass


class StrategyError(Exception):
    """策略错误"""
    pass
