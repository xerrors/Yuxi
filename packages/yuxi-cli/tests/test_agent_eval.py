from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from yuxi_cli.agent_eval import AgentEvalError, AgentEvalOptions, extract_query, run_langfuse_agent_experiment
from yuxi_cli.config import ConfigStore, Remote


class FakeResult:
    def __init__(self, outputs: list[str]):
        self.item_results = outputs
        self.output = outputs[0] if outputs else ""

    @classmethod
    def single(cls, output: str):
        return cls([output])

    def format(self, *, include_item_results: bool) -> str:
        assert include_item_results is True
        return f"formatted: {self.output}"


class FakeDataset:
    def __init__(self, item, *, result: FakeResult | None = None):
        self.item = item
        self.items = [item]
        self.result = result
        self.run_kwargs = None

    def run_experiment(self, **kwargs):
        self.run_kwargs = kwargs
        output = kwargs["task"](item=self.item)
        return self.result or FakeResult.single(output)


class FakePartialDataset:
    def __init__(self):
        self.items = [
            SimpleNamespace(id="item-1", input="hello"),
            SimpleNamespace(id="item-2", input="world"),
        ]
        self.run_kwargs = None

    def run_experiment(self, **kwargs):
        self.run_kwargs = kwargs
        kwargs["task"](item=self.items[0])
        return FakeResult.single("final answer")


class FakeLangfuse:
    def __init__(self, dataset):
        self.dataset = dataset
        self.flushed = 0

    def get_dataset(self, name: str):
        assert name == "agent-eval-smoke"
        return self.dataset

    def flush(self):
        self.flushed += 1


class FakeYuxiClient:
    calls = []

    def __init__(self, remote: Remote, timeout: float = 30.0):
        self.remote = remote
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def run_agent_eval(self, **kwargs):
        self.calls.append({"remote": self.remote, "client_timeout": self.timeout, "kwargs": kwargs})
        return {"status": "completed", "output": "final answer"}


def _console():
    return Console(file=io.StringIO(), force_terminal=False)


def test_extract_query_supports_string_and_common_fields():
    assert extract_query("hello") == "hello"
    assert extract_query({"query": "hello"}) == "hello"
    assert extract_query({"question": "hello"}) == "hello"
    assert extract_query({"prompt": "hello"}) == "hello"


def test_extract_query_rejects_unrecognized_input():
    with pytest.raises(AgentEvalError, match="无法从 Langfuse dataset item input 中提取 query"):
        extract_query({"text": "hello"})


def test_run_langfuse_agent_experiment_uses_remote_api_key(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    remote = config.get_remote("local")
    remote.api_key = "yxkey_local"
    store.save(config)
    dataset = FakeDataset(SimpleNamespace(id="item-1", input={"query": "2+2=?"}))
    langfuse = FakeLangfuse(dataset)
    console = _console()
    FakeYuxiClient.calls = []

    run_langfuse_agent_experiment(
        store,
        None,
        AgentEvalOptions(
            dataset_name="agent-eval-smoke",
            agent_slug="default-chatbot",
            experiment_name="exp-1",
            max_concurrency=2,
            timeout_seconds=123,
        ),
        console,
        langfuse_factory=lambda: langfuse,
        client_factory=FakeYuxiClient,
    )

    assert dataset.run_kwargs["name"] == "exp-1"
    assert dataset.run_kwargs["max_concurrency"] == 2
    assert dataset.run_kwargs["metadata"] == {
        "source": "agent_evaluation",
        "agent_slug": "default-chatbot",
        "dataset_name": "agent-eval-smoke",
        "remote": "local",
    }
    call = FakeYuxiClient.calls[0]
    assert call["remote"].name == "local"
    assert call["client_timeout"] == 123
    assert call["kwargs"]["query"] == "2+2=?"
    assert call["kwargs"]["agent_slug"] == "default-chatbot"
    assert "api_key" not in call["kwargs"]
    assert call["kwargs"]["timeout_seconds"] == 123
    assert call["kwargs"]["evaluation"] == {
        "dataset_name": "agent-eval-smoke",
        "dataset_item_id": "item-1",
        "experiment_name": "exp-1",
    }
    assert call["kwargs"]["meta"]["request_id"].startswith("eval-")
    assert "formatted: final answer" in console.file.getvalue()
    assert langfuse.flushed == 1


def test_run_langfuse_agent_experiment_requires_login(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")

    with pytest.raises(AgentEvalError, match="remote 尚未登录"):
        run_langfuse_agent_experiment(
            store,
            None,
            AgentEvalOptions(dataset_name="agent-eval-smoke", agent_slug="default-chatbot"),
            _console(),
            langfuse_factory=lambda: FakeLangfuse(FakeDataset(SimpleNamespace(id="1", input="hello"))),
            client_factory=FakeYuxiClient,
        )


def test_run_langfuse_agent_experiment_rejects_partial_langfuse_results(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = store.load()
    config.get_remote("local").api_key = "yxkey_local"
    store.save(config)
    langfuse = FakeLangfuse(FakePartialDataset())
    FakeYuxiClient.calls = []

    with pytest.raises(AgentEvalError, match="1/2 个 item 成功写入"):
        run_langfuse_agent_experiment(
            store,
            None,
            AgentEvalOptions(
                dataset_name="agent-eval-smoke",
                agent_slug="default-chatbot",
                experiment_name="exp-1",
            ),
            _console(),
            langfuse_factory=lambda: langfuse,
            client_factory=FakeYuxiClient,
        )
