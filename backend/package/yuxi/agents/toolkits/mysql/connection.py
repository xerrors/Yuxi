import concurrent.futures
import threading
import time
from contextlib import contextmanager
from typing import Any

import pymysql
from pymysql import MySQLError
from pymysql.cursors import DictCursor

from yuxi.utils import logger


class MySQLConnectionManager:
    """MySQL 数据库连接管理器"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.connection = None
        self._lock = threading.Lock()
        self.last_connection_time = 0
        self.max_connection_age = 3600  # 1小时后重新连接

    def _get_connection(self) -> pymysql.Connection:
        """获取数据库连接"""
        current_time = time.time()

        # 检查连接是否过期或断开
        if (
            self.connection is None
            or not self.connection.open
            or current_time - self.last_connection_time > self.max_connection_age
        ):
            with self._lock:
                # 双重检查
                if (
                    self.connection is None
                    or not self.connection.open
                    or current_time - self.last_connection_time > self.max_connection_age
                ):
                    # 关闭旧连接
                    if self.connection and self.connection.open:
                        try:
                            self.connection.close()
                        except Exception as _:
                            pass

                    # 创建新连接
                    self.connection = self._create_connection()
                    self.last_connection_time = current_time

        return self.connection

    def _create_connection(self) -> pymysql.Connection:
        """创建新的数据库连接"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                connection = pymysql.connect(
                    host=self.config["host"],
                    user=self.config["user"],
                    password=self.config["password"],
                    database=self.config["database"],
                    port=self.config["port"],
                    charset=self.config.get("charset", "utf8mb4"),
                    cursorclass=DictCursor,
                    connect_timeout=10,
                    read_timeout=60,  # 增加读取超时
                    write_timeout=30,
                    autocommit=True,  # 自动提交
                )
                logger.info(f"MySQL connection established successfully (attempt {attempt + 1})")
                return connection

            except MySQLError as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)  # 指数退避
                else:
                    logger.error(f"Failed to connect to MySQL after {max_retries} attempts: {e}")
                    raise ConnectionError(f"MySQL connection failed: {e}")

    def test_connection(self) -> bool:
        """测试连接是否有效"""
        try:
            if self.connection and self.connection.open:
                # 执行简单查询测试连接
                with self.connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                return True
        except Exception as _:
            pass
        return False

    def _invalidate_connection(self, connection: pymysql.Connection | None = None):
        """关闭并清理失效的连接"""
        try:
            if connection:
                connection.close()
        except Exception:
            pass
        finally:
            self.connection = None

    @contextmanager
    def get_cursor(self):
        """获取数据库游标的上下文管理器"""
        max_retries = 2
        cursor = None
        connection = None
        last_error: Exception | None = None

        # 优先确保成功获取游标再交给调用方执行查询
        for attempt in range(max_retries):
            try:
                connection = self._get_connection()
                cursor = connection.cursor()
                break
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to acquire cursor (attempt {attempt + 1}): {e}")
                self._invalidate_connection(connection)
                cursor = None
                connection = None
                if attempt == max_retries - 1:
                    raise e
                time.sleep(1)

        if cursor is None or connection is None:
            raise last_error or ConnectionError("Unable to acquire MySQL cursor")

        try:
            yield cursor
            connection.commit()
        except Exception as e:
            try:
                connection.rollback()
            except Exception:
                pass

            # 标记连接失效，等待下一次获取时重建
            if "MySQL" in str(e) or "connection" in str(e).lower():
                logger.warning(f"MySQL connection error encountered, invalidating connection: {e}")
                self._invalidate_connection(connection)

            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

    def close(self):
        """关闭数据库连接"""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.info("MySQL connection closed")

    def get_connection(self) -> pymysql.Connection:
        """对外暴露的连接获取方法"""
        return self._get_connection()

    def invalidate_connection(self):
        """手动标记连接失效"""
        self._invalidate_connection(self.connection)

    @property
    def database_name(self) -> str:
        """返回当前配置的数据库名称"""
        return self.config["database"]


class QueryTimeoutError(Exception):
    """查询超时异常"""

    pass


class QueryResultTooLargeError(Exception):
    """查询结果过大异常"""

    pass


def execute_query_with_timeout(connection: pymysql.Connection, sql: str, params: tuple = None, timeout: int = 10):
    """使用线程池实现超时控制，避免信号导致的生成器问题"""

    def query_worker():
        """查询工作函数，在单独线程中执行"""
        cursor = connection.cursor(DictCursor)
        try:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            result = cursor.fetchall()
            return result
        finally:
            cursor.close()

    # 使用线程池执行查询，设置超时
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(query_worker)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # 尝试取消任务
            future.cancel()
            raise QueryTimeoutError(f"Query timeout after {timeout} seconds")


def limit_result_size(result: list, max_chars: int = 10000) -> list:
    """限制结果大小"""
    if not result:
        return result

    # 计算结果的字符大小
    result_str = str(result)
    if len(result_str) > max_chars:
        # 返回部分结果并提示
        limited_result = []
        current_chars = 0
        for row in result:
            row_str = str(row)
            if current_chars + len(row_str) > max_chars:
                break
            limited_result.append(row)
            current_chars += len(row_str)

        # 记录警告
        logger.warning(f"Query result truncated from {len(result)} to {len(limited_result)} rows due to size limit")
        return limited_result

    return result
