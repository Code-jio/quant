"""
数据模块异常定义
"""


class DatabaseError(Exception):
    """数据库异常"""
    pass


class DataLoadError(Exception):
    """数据加载异常"""
    pass
