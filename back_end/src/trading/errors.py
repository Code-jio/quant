"""
交易模块异常定义
"""


class TradingError(Exception):
    """交易异常"""
    pass


class GatewayError(Exception):
    """网关异常"""
    pass
