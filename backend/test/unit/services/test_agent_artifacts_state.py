from yuxi.agents.backends.sandbox import (
    VIRTUAL_PATH_PREFIX,
    ensure_thread_dirs,
    sandbox_outputs_dir,
    sandbox_uploads_dir,
)
from yuxi.agents.buildin.chatbot.state import merge_subagent_runs
from yuxi.agents.state import merge_artifacts
from yuxi.agents.toolkits.buildin.tools import _normalize_presented_artifact_path
from yuxi.services.chat_service import extract_agent_state
from yuxi.utils.paths import CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME


def _runtime_with_thread(thread_id: str, uid: str = "user-1"):
    context = type("RuntimeContext", (), {"thread_id": thread_id, "uid": uid})()
    return type("RuntimeStub", (), {"context": context})()


def test_merge_artifacts_deduplicates_and_preserves_order():
    assert merge_artifacts(
        ["/home/gem/user-data/outputs/a.md"],
        ["/home/gem/user-data/outputs/a.md", "/home/gem/user-data/outputs/b.md"],
    ) == [
        "/home/gem/user-data/outputs/a.md",
        "/home/gem/user-data/outputs/b.md",
    ]


def test_merge_subagent_runs_updates_existing_run_by_id():
    assert merge_subagent_runs(
        [{"id": "run-1", "status": "completed", "result_preview": "old"}],
        [
            {"id": "run-1", "status": "failed", "error": "boom"},
            {"id": "run-2", "status": "completed"},
        ],
    ) == [
        {"id": "run-1", "status": "failed", "result_preview": "old", "error": "boom"},
        {"id": "run-2", "status": "completed"},
    ]


def test_normalize_presented_artifact_path_accepts_host_path():
    thread_id = "artifacts-host-path"
    ensure_thread_dirs(thread_id, "user-1")
    output_file = sandbox_outputs_dir(thread_id) / "report.md"
    output_file.write_text("# demo", encoding="utf-8")

    normalized = _normalize_presented_artifact_path(str(output_file), _runtime_with_thread(thread_id))

    assert normalized == f"{VIRTUAL_PATH_PREFIX}/outputs/report.md"


def test_normalize_presented_artifact_path_accepts_virtual_path():
    thread_id = "artifacts-virtual-path"
    ensure_thread_dirs(thread_id, "user-1")
    output_file = sandbox_outputs_dir(thread_id) / "summary.txt"
    output_file.write_text("demo", encoding="utf-8")

    normalized = _normalize_presented_artifact_path(
        f"{VIRTUAL_PATH_PREFIX}/outputs/summary.txt",
        _runtime_with_thread(thread_id),
    )

    assert normalized == f"{VIRTUAL_PATH_PREFIX}/outputs/summary.txt"


def test_normalize_presented_artifact_path_rejects_non_outputs_path():
    thread_id = "artifacts-reject-path"
    ensure_thread_dirs(thread_id, "user-1")
    upload_file = sandbox_uploads_dir(thread_id) / "note.txt"
    upload_file.write_text("demo", encoding="utf-8")

    try:
        _normalize_presented_artifact_path(str(upload_file), _runtime_with_thread(thread_id))
    except ValueError as exc:
        assert f"{VIRTUAL_PATH_PREFIX}/outputs/" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-outputs file")


def test_normalize_presented_artifact_path_rejects_internal_output_files():
    thread_id = "artifacts-reject-internal"
    ensure_thread_dirs(thread_id, "user-1")

    for dir_name in [LARGE_TOOL_RESULTS_DIR_NAME, CONVERSATION_HISTORY_DIR_NAME, "large_tool_history"]:
        output_file = sandbox_outputs_dir(thread_id) / dir_name / "stage.txt"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("internal", encoding="utf-8")

        try:
            _normalize_presented_artifact_path(str(output_file), _runtime_with_thread(thread_id))
        except ValueError as exc:
            assert "工具调用阶段文件" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for internal output file under {dir_name}")


def test_extract_agent_state_includes_artifacts():
    state = extract_agent_state(
        {
            "todos": [{"content": "done", "status": "completed"}],
            "files": {"/tmp/demo.txt": {"content": ["x"]}},
            "artifacts": ["/home/gem/user-data/outputs/demo.txt"],
            "subagent_runs": [{"id": "tool-1", "status": "completed"}],
            "token_usage": {"llm_input_tokens": 42},
        }
    )

    assert state["todos"] == [{"content": "done", "status": "completed"}]
    assert state["files"] == {"/tmp/demo.txt": {"content": ["x"]}}
    assert state["artifacts"] == ["/home/gem/user-data/outputs/demo.txt"]
    assert state["subagent_runs"] == [{"id": "tool-1", "status": "completed"}]
    assert state["token_usage"] == {"llm_input_tokens": 42}
