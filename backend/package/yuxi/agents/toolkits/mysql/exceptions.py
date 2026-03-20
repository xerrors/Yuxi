class MySQLToolError(Exception):
    """MySQL 工具基础异常"""

    pass


class MySQLConnectionError(MySQLToolError):
    """MySQL 连接异常"""

    pass


class MySQLQueryError(MySQLToolError):
    """MySQL 查询异常"""

    pass


class MySQLSecurityError(MySQLToolError):
    """MySQL 安全异常"""

    pass


class MySQLTimeoutError(MySQLToolError):
    """MySQL 超时异常"""

    pass


class MySQLResultTooLargeError(MySQLToolError):
    """MySQL 结果过大异常"""

    pass
