"""注册分域 ORM，并为 SQLite 单测提供 Run 序号替身。"""

from itertools import count

import pytest
from sqlalchemy import event

from yuxi.modules.agents.models.runs import AgentRun
from yuxi.infrastructure.postgres.models import load_models

load_models()


@pytest.fixture(scope="session", autouse=True)
def isolated_user_data(tmp_path_factory):
    """单测的文件副作用与用量标记只存放在 pytest 临时目录。"""
    with pytest.MonkeyPatch.context() as env:
        env.setenv("YUXI_USER_DATA_DIR", str(tmp_path_factory.mktemp("user-data")))
        yield


@pytest.fixture(autouse=True)
def sqlite_run_execution_sequence():
    """SQLite 无 sequence，插入测试 Run 时按提交顺序补序号。"""
    sequence = count(1)

    def assign_sequence(_mapper, connection, run: AgentRun) -> None:
        if connection.dialect.name == "sqlite" and run.execution_seq is None:
            run.execution_seq = next(sequence)

    event.listen(AgentRun, "before_insert", assign_sequence)
    try:
        yield
    finally:
        event.remove(AgentRun, "before_insert", assign_sequence)
