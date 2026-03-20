import re


class MySQLSecurityChecker:
    """MySQL 安全检查器"""

    # 允许的SQL操作
    ALLOWED_OPERATIONS = {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN"}

    # 危险的关键词
    DANGEROUS_KEYWORDS = {
        "DROP",
        "DELETE",
        "UPDATE",
        "INSERT",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "REPLACE",
        "LOAD",
        "GRANT",
        "REVOKE",
        "SET",
        "COMMIT",
        "ROLLBACK",
        "UNLOCK",
        "KILL",
        "SHUTDOWN",
    }

    @classmethod
    def validate_sql(cls, sql: str) -> bool:
        """验证SQL语句的安全性"""
        if not sql:
            return False

        # 移除SQL注释（-- 和 /* */）后再验证
        sql_clean = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
        sql_clean = re.sub(r"/\*.*?\*/", "", sql_clean)
        sql_upper = sql_clean.strip().upper()

        # 检查是否是允许的操作
        if not any(sql_upper.startswith(op) for op in cls.ALLOWED_OPERATIONS):
            return False

        # 检查危险关键词 - 只检查语句开头的关键字，避免列名/表名误报
        # 提取语句开头的第一个词
        first_word_match = re.match(r"^\s*(\w+)", sql_upper)
        first_word = first_word_match.group(1) if first_word_match else ""

        # 只在开头检查危险关键词
        if first_word in cls.DANGEROUS_KEYWORDS:
            return False

        # 检查SQL注入模式
        sql_injection_patterns = [
            r"\bor\s+1\s*=\s*1\b",
            r"\bunion\s+select\b",
            r"\bexec\s*\(",
            r"\bxp_cmdshell\b",
            r"\bsleep\s*\(",
            r"\bbenchmark\s*\(",
            r"\bwaitfor\s+delay\b",
            r"\b;\s*drop\b",
            r"\b;\s*delete\b",
            r"\b;\s*update\b",
            r"\b;\s*insert\b",
        ]

        for pattern in sql_injection_patterns:
            if re.search(pattern, sql_upper, re.IGNORECASE):
                return False

        return True

    @classmethod
    def validate_table_name(cls, table_name: str) -> bool:
        """验证表名的安全性"""
        if not table_name:
            return False

        # 检查表名只包含字母、数字、下划线
        return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name))

    @classmethod
    def validate_timeout(cls, timeout: int) -> bool:
        """验证timeout参数"""
        return isinstance(timeout, int) and 1 <= timeout <= 600
