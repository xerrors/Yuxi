from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from yuxi.agents.skills.buildin import BUILTIN_SKILLS_DIR
from yuxi.agents.skills.service import copy_skill_tree_no_symlinks


def _mysql_reporter_dir() -> Path:
    return BUILTIN_SKILLS_DIR / "mysql-reporter"


@contextmanager
def _script_import_path():
    script_dir = str(_mysql_reporter_dir() / "scripts")
    if script_dir in sys.path:
        yield
        return

    sys.path.insert(0, script_dir)
    try:
        yield
    finally:
        sys.path.remove(script_dir)


def _load_script(script_name: str) -> ModuleType:
    script_path = _mysql_reporter_dir() / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(f"mysql_reporter_{script_path.stem}", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with _script_import_path():
        spec.loader.exec_module(module)
    return module


def _load_common() -> ModuleType:
    with _script_import_path():
        return importlib.import_module("_mysql_common")


def test_mysql_reporter_scripts_share_common_connection_helpers():
    common = _load_common()

    for script_name in ("list_tables.py", "describe_table.py", "query.py"):
        script = _load_script(script_name)
        assert script.load_mysql_config is common.load_mysql_config
        assert script.create_connection is common.create_connection


def test_mysql_reporter_common_config_requires_connection_environment(monkeypatch):
    common = _load_common()
    for key in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE", "MYSQL_PORT"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(common.MySQLConnectionError, match="host"):
        common.load_mysql_config()

    monkeypatch.setenv("MYSQL_HOST", "mysql.example")
    monkeypatch.setenv("MYSQL_USER", "reporter")
    monkeypatch.setenv("MYSQL_PASSWORD", "secret")
    monkeypatch.setenv("MYSQL_DATABASE", "sales")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_DATABASE_DESCRIPTION", "")

    assert common.load_mysql_config() == {
        "host": "mysql.example",
        "user": "reporter",
        "password": "secret",
        "database": "sales",
        "port": 3307,
        "charset": "utf8mb4",
        "description": "默认 MySQL 数据库",
    }


@pytest.mark.parametrize(
    ("script_name", "args"),
    [
        ("list_tables.py", []),
        ("describe_table.py", ["--table", "users"]),
        ("query.py", ["--sql", "SELECT 1"]),
    ],
)
def test_mysql_reporter_projected_cli_loads_common_helper_and_fails_without_config(tmp_path, script_name, args):
    projected_skill = tmp_path / "mysql-reporter"
    copy_skill_tree_no_symlinks(_mysql_reporter_dir(), projected_skill)
    env = os.environ.copy()
    for key in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE", "MYSQL_PORT"):
        env.pop(key, None)

    result = subprocess.run(
        [sys.executable, str(projected_skill / "scripts" / script_name), *args],
        cwd=projected_skill,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "configuration missing required key: host" in result.stderr


def test_mysql_reporter_query_security_validates_sql_and_timeout():
    query_script = _load_script("query.py")
    sql_cases = {
        "": False,
        "SELECT * FROM users": True,
        "show tables": True,
        "DESCRIBE users": True,
        "EXPLAIN SELECT * FROM users": True,
        "SELECT 1;": True,
        "DELETE FROM users": False,
        "SELECT * FROM users WHERE id = 1 OR 1=1": False,
        "SELECT * FROM users UNION SELECT password FROM admin": False,
        "SELECT 'DROP' AS keyword_text": True,
        "/* comment */ SELECT 1": True,
        "/* multi\nline */ SELECT 1": True,
        "SELECT * FROM users; DROP TABLE users": False,
        "SELECT * FROM users; CREATE TABLE audit_log(id INT)": False,
        "SELECT * FROM users; SET @unsafe = 1": False,
    }

    for sql, expected in sql_cases.items():
        assert query_script.MySQLSecurityChecker.validate_sql(sql) is expected

    timeout_cases = {
        None: False,
        0: False,
        1: True,
        60: True,
        600: True,
        601: False,
        "60": False,
    }

    for timeout, expected in timeout_cases.items():
        assert query_script.MySQLSecurityChecker.validate_timeout(timeout) is expected


def test_mysql_reporter_describe_table_name_security_validates_known_cases():
    describe_script = _load_script("describe_table.py")
    table_cases = {
        "": False,
        "users": True,
        "_audit_log": True,
        "user_2026": True,
        "1users": False,
        "user-name": False,
        "users;drop": False,
    }

    for table_name, expected in table_cases.items():
        assert describe_script.MySQLSecurityChecker.validate_table_name(table_name) is expected
