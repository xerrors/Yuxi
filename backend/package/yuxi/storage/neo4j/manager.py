from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from typing import Any

from yuxi.utils import logger

from neo4j import GraphDatabase as GD

_SAFE_NEO4J_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_shared_neo4j_connection: Neo4jConnectionManager | None = None
_shared_neo4j_connection_lock = threading.Lock()


def safe_neo4j_label(value: str) -> str:
    if not _SAFE_NEO4J_LABEL_RE.match(value or ""):
        raise ValueError(f"非法 Neo4j 标签: {value}")
    return value


def neo4j_write(driver, query: Callable) -> Any:
    """在写事务中执行 Cypher 操作的简写。"""
    with driver.session() as session:
        return session.execute_write(query)


def neo4j_read(driver, cypher: str, **kwargs) -> list[dict[str, Any]]:
    """执行只读 Cypher 查询并返回结果列表。"""
    with driver.session() as session:
        result = session.run(cypher, **kwargs)
        return [record.data() for record in result]


class Neo4jConnectionManager:
    def __init__(self):
        self.driver = None
        self.status = "closed"
        if os.environ.get("LITE_MODE", "").lower() in ("true", "1"):
            logger.info("LITE_MODE enabled, skipping Neo4j connection")
            return
        self._connect()

    def _connect(self):
        if self.driver and self._is_connected():
            return

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        username = os.environ.get("NEO4J_USERNAME", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "0123456789")

        try:
            self.driver = GD.driver(uri, auth=(username, password))
            with self.driver.session() as session:
                session.run("RETURN 1")
            self.status = "open"
            logger.info("Successfully connected to Neo4j")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            raise

    def _is_connected(self) -> bool:
        if not self.driver:
            return False
        try:
            with self.driver.session() as session:
                session.run("RETURN 1")
            return True
        except Exception:
            return False

    def is_running(self):
        return self.status == "open" or self.status == "processing"

    def close(self):
        if self.driver:
            self.driver.close()
            self.driver = None
            self.status = "closed"


def get_shared_neo4j_connection() -> Neo4jConnectionManager:
    global _shared_neo4j_connection
    if _shared_neo4j_connection is None or not _shared_neo4j_connection.driver:
        with _shared_neo4j_connection_lock:
            if _shared_neo4j_connection is None or not _shared_neo4j_connection.driver:
                _shared_neo4j_connection = Neo4jConnectionManager()
    return _shared_neo4j_connection


def close_shared_neo4j_connection() -> None:
    with _shared_neo4j_connection_lock:
        if _shared_neo4j_connection is not None:
            _shared_neo4j_connection.close()
