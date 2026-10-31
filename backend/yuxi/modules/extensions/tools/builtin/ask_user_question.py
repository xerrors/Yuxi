"""拥有提问参数契约并保存标准等待点。"""

from typing import Self

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuxi.modules.extensions.tools.registry import tool


class QuestionOption(BaseModel):
    """选择题的标准选项。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    label: str = Field(min_length=1, description="向用户展示的选项名称")
    value: str = Field(min_length=1, description="提交答案时使用的选项值")
    description: str | None = Field(
        default=None,
        description="可选的简短说明；必要时解释选项含义、适用场景或关键取舍，含义明确时省略，避免重复 label",
    )


class UserQuestion(BaseModel):
    """标准问题及显示策略。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    question: str = Field(min_length=1, description="需要用户回答的问题")
    question_id: str | None = Field(default=None, min_length=1, description="可选的唯一问题 ID；省略时生成")
    options: list[QuestionOption] = Field(default_factory=list, max_length=5, description="纯问答留空，选择题提供选项")
    multi_select: bool = Field(default=False, description="是否允许多选")
    allow_other: bool = Field(default=True, description="选择题是否允许自行填写")
    operation: str | None = Field(default=None, min_length=1, description="可选的操作说明")


class AskUserQuestionInput(BaseModel):
    """在工具入口校验问题并确定回答键。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    questions: list[UserQuestion] = Field(min_length=1, max_length=5, description="需要用户回答的 1-5 个问题")

    @model_validator(mode="after")
    def assign_question_ids(self) -> Self:
        """补齐确定性 ID，拒绝回答键冲突。"""
        ids: set[str] = set()
        for index, question in enumerate(self.questions, start=1):
            question.question_id = question.question_id or f"q-{index}"
            if question.question_id in ids:
                raise ValueError(f"重复的 question_id: {question.question_id}")
            ids.add(question.question_id)
        return self


ASK_USER_QUESTION_DESCRIPTION = """
在执行过程中，当你需要用户做决定或补充需求时，使用这个工具向用户提问。

适用场景：
1. 收集用户偏好或需求（例如风格、范围、优先级）
2. 澄清模糊指令（存在多种合理解释时）
3. 在实现过程中让用户选择方案方向
4. 在有明显权衡时让用户做取舍

使用规范：
1. questions 提供 1-5 个问题，每项包含 question，并可包含 options、multi_select、allow_other
2. 纯问答不提供 options（或传空列表），用户将直接填写文本
3. 选择题的 options 提供 2-5 个有区分度的选项，每项包含 label 和 value
4. label 保持简短；必要时用 description 补充一句说明，解释选项含义、适用场景或关键取舍，帮助用户理解和区分选项。
   含义明确的选项省略 description，不重复 label，也不为每个选项机械添加说明
5. 若有推荐选项：把推荐项放在第一位，并在 label 末尾加 "(Recommended)"
6. 若需要多选：将该问题的 multi_select 设为 true
7. allow_other 只用于选择题，通常保持 true，让用户可自行填写答案

注意事项：
1. 不要用这个工具询问“是否继续执行”“计划是否准备好”这类流程控制问题
2. 不要在信息已充分、无需用户决策时滥用该工具
3. 先基于现有上下文自行决策，只有关键不确定性时才提问

返回结果：
answer 为 object，格式为 {question_id: answer}。
跳过的问题不会出现在 answer 中。answer 的值可能是 string（纯问答或单选）、list（多选）
或 object（选择题的自行填写文本）。
"""


@tool(
    category="builtin",
    tags=["交互"],
    display_name="向用户提问",
    description=ASK_USER_QUESTION_DESCRIPTION,
    args_schema=AskUserQuestionInput,
)
def ask_user_question(questions: list[UserQuestion]) -> dict:
    """保存入口校验后的问题并等待用户回答。"""
    normalized_questions = [question.model_dump(exclude_none=True) for question in questions]
    interrupt_payload = {
        "questions": normalized_questions,
        "source": "ask_user_question",
    }
    answer = interrupt(interrupt_payload)

    return {
        "questions": normalized_questions,
        "answer": answer,
    }
