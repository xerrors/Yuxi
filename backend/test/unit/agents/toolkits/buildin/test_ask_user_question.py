"""验证提问工具入口、标准等待点与非法参数拒绝。"""

import importlib
import json

from langchain_core.messages import AIMessage
from langchain_core.tools import tool as langchain_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
import pytest
from pydantic import ValidationError

from yuxi.modules.extensions.tools import builtin
from yuxi.modules.extensions.tools.registry import get_all_tool_instances, get_extra_metadata

tools = importlib.import_module("yuxi.modules.extensions.tools.builtin.ask_user_question")


def test_ask_user_question_interrupt_payload_and_result_format(monkeypatch):
    """入口补齐默认字段和 ID，返回与等待点一致的问题。"""
    captured = []
    answer = {"style": "simple", "q-2": "杭州"}
    monkeypatch.setattr(tools, "interrupt", lambda payload: captured.append(payload) or answer)

    result = tools.ask_user_question.invoke(
        {
            "questions": [
                {
                    "question_id": " style ",
                    "question": " 选择界面风格 ",
                    "options": [
                        {"label": " 简洁 (Recommended) ", "value": " simple ", "description": " 简洁布局 "},
                        {"label": "详细", "value": "detailed"},
                    ],
                    "multi_select": True,
                    "allow_other": False,
                    "operation": " 选择风格 ",
                },
                {"question": "你想去哪个城市？"},
            ]
        }
    )

    expected = [
        {
            "question_id": "style",
            "question": "选择界面风格",
            "options": [
                {"label": "简洁 (Recommended)", "value": "simple", "description": "简洁布局"},
                {"label": "详细", "value": "detailed"},
            ],
            "multi_select": True,
            "allow_other": False,
            "operation": "选择风格",
        },
        {
            "question_id": "q-2",
            "question": "你想去哪个城市？",
            "options": [],
            "multi_select": False,
            "allow_other": True,
        },
    ]
    assert captured == [{"questions": expected, "source": "ask_user_question"}]
    assert result == {"questions": expected, "answer": answer}
    assert "纯问答不提供 options" in tools.ASK_USER_QUESTION_DESCRIPTION


@pytest.mark.parametrize(
    "questions",
    [
        [],
        None,
        '[{"question":"问题"}]',
        {"items": [{"question": "问题"}]},
        [{"title": "别名问题"}],
        [{"question": " "}],
        [{"question": 123}],
        [{"question": "有效问题"}, {"question": " "}],
        [{"question": "问题", "options": ["A", "B"]}],
        [{"question": "问题", "options": {"items": []}}],
        [{"question": "问题", "options": [{"label": "A"}]}],
        [{"question": "问题", "options": [{"label": "A", "value": " "}]}],
        [{"question": "问题", "multi_select": "false"}],
        [{"question": "问题", "allow_other": 1}],
        [{"question": "问题", "question_id": " "}],
        [{"question": "问题", "operation": " "}],
        [{"question": "问题", "options": [{"label": "A", "value": "a"}] * 6}],
        [{"question": "问题"}] * 6,
    ],
)
def test_invalid_question_parameters_fail_before_interrupt(monkeypatch, questions):
    """错误参数必须整体拒绝，不能过滤后产生等待点。"""

    def unexpected_interrupt(_payload):
        raise AssertionError("非法参数不能产生等待点")

    monkeypatch.setattr(tools, "interrupt", unexpected_interrupt)
    with pytest.raises(ValidationError):
        tools.ask_user_question.invoke({"questions": questions})


@pytest.mark.parametrize("ids", [("same", "same"), (" same ", "same"), ("q-2", None)])
def test_duplicate_question_ids_are_rejected(monkeypatch, ids):
    """显式或生成的 ID 冲突不能覆盖回答。"""
    monkeypatch.setattr(tools, "interrupt", lambda _payload: pytest.fail("重复 ID 不应暂停"))
    questions = [{"question": "问题", **({"question_id": id_} if id_ else {})} for id_ in ids]
    with pytest.raises(ValidationError, match="重复的 question_id"):
        tools.ask_user_question.invoke({"questions": questions})


def test_question_ids_are_deterministic_across_reexecution(monkeypatch):
    """恢复重执行不改变省略 ID 的回答键。"""
    captured = []
    monkeypatch.setattr(tools, "interrupt", lambda payload: captured.append(payload) or {"q-1": "回答"})
    for _ in range(2):
        tools.ask_user_question.invoke({"questions": [{"question": "问题"}]})
    assert captured[0] == captured[1]
    assert captured[0]["questions"][0]["question_id"] == "q-1"


def test_model_visible_schema_has_typed_questions_and_options():
    """模型 schema 明确问题和选项字段，不暴露宽松字典或字符串分支。"""
    schema = tools.ask_user_question.args_schema.model_json_schema()
    questions = schema["properties"]["questions"]
    assert questions["type"] == "array"
    assert questions["minItems"] == 1 and questions["maxItems"] == 5
    question_schema = schema["$defs"]["UserQuestion"]
    assert "question" in question_schema["required"]
    assert question_schema["properties"]["multi_select"]["type"] == "boolean"
    assert question_schema["properties"]["options"]["items"]["$ref"].endswith("/QuestionOption")
    assert schema["$defs"]["QuestionOption"]["required"] == ["label", "value"]


def test_split_builtin_tools_are_registered_once_with_stable_metadata():
    """真实包导入装配每个内置工具一次并保留工具名称。"""
    for name in ("ask_user_question", "ocr_parse_file", "present_artifacts"):
        instance = getattr(builtin, name)
        assert instance.name == name
        assert sum(tool is instance for tool in get_all_tool_instances()) == 1
        assert sum(tool.name == name for tool in get_all_tool_instances()) == 1
        assert get_extra_metadata(name).category == "builtin"


@pytest.mark.parametrize(
    "raw_question, can_resume",
    [
        ({"question": "选择？", "options": [{"label": "A", "value": "A"}]}, True),
        ({"question": "选择？", "options": ["A"]}, False),
        ({"question": "选择？", "multi_select": "false"}, False),
    ],
)
def test_checkpoint_replay_preserves_standard_calls_and_requires_legacy_drain(raw_question, can_resume):
    """旧工具产生的真实 checkpoint 只在原始参数满足标准 schema 时可直接恢复。"""
    standard_questions = [
        {
            "question_id": "q-1",
            "question": "选择？",
            "options": [{"label": "A", "value": "A"}] if raw_question.get("options") else [],
            "multi_select": False,
            "allow_other": True,
        }
    ]

    @langchain_tool("ask_user_question")
    def old_question_tool(questions: list[dict]) -> dict:
        """模拟旧入口接受宽松参数并保存标准问题。"""
        return {
            "questions": standard_questions,
            "answer": tools.interrupt(
                {
                    "questions": standard_questions,
                    "source": "ask_user_question",
                }
            ),
        }

    saver = InMemorySaver()

    def compile_graph(question_tool):
        """构造共用 checkpoint 的真实工具图。"""
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([question_tool]))
        builder.set_entry_point("tools")
        builder.set_finish_point("tools")
        return builder.compile(checkpointer=saver)

    config = {"configurable": {"thread_id": "question-contract-upgrade"}}
    old_graph = compile_graph(old_question_tool)
    initial = old_graph.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "ask-1",
                            "name": "ask_user_question",
                            "args": {"questions": [raw_question]},
                        }
                    ],
                )
            ]
        },
        config,
    )
    assert initial["__interrupt__"][0].value["questions"] == standard_questions
    assert old_graph.get_state(config).values["messages"][-1].tool_calls[0]["args"] == {"questions": [raw_question]}

    answer = {"q-1": "A"}
    new_graph = compile_graph(tools.ask_user_question)
    resumed = new_graph.invoke(Command(resume=answer), config)
    result = resumed["messages"][-1]
    assert not new_graph.get_state(config).interrupts
    if can_resume:
        assert result.status == "success"
        assert json.loads(result.content) == {"questions": standard_questions, "answer": answer}
    else:
        assert result.status == "error"
        assert "Error invoking tool" in result.content


def test_personal_skill_installer_is_not_registered():
    """个人安装交给沙盒 CLI，模型工具目录不再暴露专用安装工具。"""
    from yuxi.modules.extensions.tools.catalog import get_tool_metadata

    assert "install_skill" not in {item["slug"] for item in get_tool_metadata()}
