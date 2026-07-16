import pytest

from yuxi.storage.neo4j import (
    Neo4jConnectionManager,
    close_shared_neo4j_connection,
    safe_neo4j_label,
)
from yuxi.storage.neo4j import manager as neo4j_manager


def test_storage_neo4j_exports_manager():
    import yuxi.storage.neo4j as neo4j_storage

    assert neo4j_storage.Neo4jConnectionManager is Neo4jConnectionManager
    assert neo4j_storage.safe_neo4j_label is safe_neo4j_label


@pytest.mark.parametrize("label", ["kb_test", "MilvusKB", "_internal_1"])
def test_safe_neo4j_label_accepts_valid_labels(label):
    assert safe_neo4j_label(label) == label


@pytest.mark.parametrize("label", ["", "1invalid", "has-dash", "has space", "中文"])
def test_safe_neo4j_label_rejects_invalid_labels(label):
    with pytest.raises(ValueError, match="非法 Neo4j 标签"):
        safe_neo4j_label(label)


def test_neo4j_connection_manager_skips_connection_in_lite_mode(monkeypatch):
    monkeypatch.setenv("LITE_MODE", "true")

    manager = Neo4jConnectionManager()

    assert manager.driver is None
    assert manager.status == "closed"


def test_close_shared_neo4j_connection_closes_existing_manager(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(neo4j_manager, "_shared_neo4j_connection", connection)

    close_shared_neo4j_connection()

    assert connection.closed is True
