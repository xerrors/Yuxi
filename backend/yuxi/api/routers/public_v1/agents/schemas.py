"""公开 Session 的请求与响应模型，分别保留输入校验与持久响应约束。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, StringConstraints, model_validator

from yuxi.modules.agents.input_messages import (
    AgentRunInputMessage,
    build_chat_input_message_from_openai_content,
    normalize_image_contents,
)


class WireModel(BaseModel):
    """拒绝未定义的 Public 输入字段。"""

    model_config = ConfigDict(extra="forbid")


class FileResponse(BaseModel):
    """隔离文件草稿的公开引用。"""

    id: str = Field(description="稳定文件 ID，通过提交级 yuxi.attachment_file_ids 引用。")
    object: Literal["file"] = "file"
    filename: str = Field(description="服务端规范化的原文件名称。")
    bytes: int = Field(description="原文件字节数，上限 5 MiB。")
    mime_type: str = Field(description="上传声明的 MIME 类型；缺省为 application/octet-stream。")
    created_at: float = Field(description="上传时间，Unix 秒。")
    expires_at: float = Field(description="未提交草稿的过期时间，Unix 秒；提交后不再按 draft 到期清理。")
    status: Literal["draft", "parsed"] = Field(description="仅描述文件准备；两者都尚未进入 Workdir。")


class InputTextPart(WireModel):
    """文本输入内容块。"""

    type: Literal["input_text"]
    text: str = Field(min_length=1, max_length=32768)


class InputImagePart(WireModel):
    """内联图片输入内容块。"""

    type: Literal["input_image"]
    image_url: str = Field(min_length=1)


class InputAttachmentOptions(WireModel):
    """仅绑定到本次 Input、消费后归属用户 Message 的附件引用。"""

    attachment_file_ids: list[Annotated[str, Field(min_length=32, max_length=32)]] = Field(default_factory=list, max_length=20)


class SessionConnectorOptions(WireModel):
    """追加到会话配置、由后续输入继承的连接器选择。"""

    mcps: list[Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]] = Field(
        default_factory=list,
        description="追加挂载到当前会话的连接器 slug；与 context MCP 取并集，后续输入沿用，执行时按当前权限解析。省略或空列表不移除选择。",
    )


class InputMessage(WireModel):
    """一条有序用户消息。"""

    type: Literal["message"] = "message"
    role: Literal["user"]
    content: list[Annotated[InputTextPart | InputImagePart, Field(discriminator="type")]] = Field(min_length=1, max_length=18)


class SessionAgentOverride(WireModel):
    """支持已保存 Agent 的模型选择覆盖。"""

    model: str | None = Field(
        default=None,
        min_length=1,
        description="聊天模型标识；创建时省略按 Agent/系统默认解析，更新时省略保持会话模型。",
    )


class SessionCreateOptions(SessionConnectorOptions, InputAttachmentOptions):
    """创建请求同时携带会话连接器选择与首批输入的附件引用。"""


class SessionCreate(WireModel):
    """创建 Session 或原子接收首批消息。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"agent_id": "default-chatbot", "input": "你好"},
                {
                    "agent_id": "default-chatbot",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "你好"}]}],
                    "stream": True,
                },
            ]
        },
    )

    agent_id: str = Field(min_length=1, max_length=64, description="可见的已保存 Agent slug，先从 Agent 目录查询。")
    yuxi: SessionCreateOptions = Field(default_factory=SessionCreateOptions)
    agent: SessionAgentOverride | None = Field(default=None, description="仅支持 model 覆盖；其他官方 Agent 配置当前不支持。")
    input: (
        Annotated[str, Field(min_length=1, max_length=32768)] | Annotated[list[InputMessage], Field(min_length=1, max_length=20)] | None
    ) = Field(
        default=None,
        description="初始用户输入；字符串等价于一条 input_text 用户消息，省略创建空会话。",
    )
    stream: StrictBool = Field(default=False, description="true 返回长期 text/event-stream；必须同时提供 input，客户端自行关闭订阅。")
    project_id: str | None = Field(default=None, description="Yuxi 扩展：当前用户的 Project；省略时创建隐式 Project。")
    title: str | None = Field(default=None, max_length=255, description="Yuxi 扩展：会话展示标题。")
    tool_approval_mode: str | None = Field(default=None, description="Yuxi 扩展：后续输入默认工具审批模式。")


class SessionUpdateOptions(WireModel):
    """更新会话展示字段和工具审批配置。"""

    title: str | None = Field(default=None, max_length=255)
    is_pinned: StrictBool | None = None
    tool_approval_mode: str | None = None


class SessionUpdate(WireModel):
    """更新会话展示字段与后续输入配置，已接收输入保持原配置。"""

    agent: SessionAgentOverride | None = None
    yuxi: SessionUpdateOptions = Field(default_factory=SessionUpdateOptions)

    @model_validator(mode="after")
    def reject_null_model_update(self):
        """模型省略表示保持原值，显式 null 不具有重置语义。"""
        if self.agent is not None and "model" in self.agent.model_fields_set and self.agent.model is None:
            raise ValueError("agent.model 不能为 null；省略字段以保持原值")
        return self


class MessageOptions(SessionConnectorOptions, InputAttachmentOptions):
    """输入请求组合会话连接器追加、本次执行覆盖和附件引用。"""

    mode: Literal["follow_up", "steer"] | None = Field(
        default=None,
        description="follow_up 按 FIFO 排队；steer 优先接管。省略时运行中选择 steer，空闲选择 follow_up，协作等待时排队。",
    )
    model: str | None = Field(default=None, min_length=1, description="本次 follow_up 的模型覆盖，与消息原子接收；steer 禁止覆盖。")
    tool_approval_mode: str | None = None


class MessageEvent(WireModel):
    """官方消息输入，业务扩展统一放入 yuxi。"""

    type: Literal["agent.session.input.message"]
    input: list[InputMessage] = Field(min_length=1, max_length=20)
    yuxi: MessageOptions = Field(default_factory=MessageOptions)


class OtherAnswer(WireModel):
    """选项外填写的文本及已选选项。"""

    type: Literal["other"]
    text: StrictStr = Field(min_length=1)
    selected: list[StrictStr]


class AnswerItem(WireModel):
    """回答等待点中的一个问题。"""

    question_id: str = Field(min_length=1)
    answer: StrictStr | Annotated[list[StrictStr], Field(min_length=1)] | OtherAnswer


class AnswerPayload(WireModel):
    """按等待点顺序回答全部问题。"""

    type: Literal["answer"]
    answers: list[AnswerItem] = Field(min_length=1)


class ApprovalDecision(WireModel):
    """对一个等待中的工具调用作出决定。"""

    call_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]


class ApprovalPayload(WireModel):
    """按等待点顺序决定全部工具调用。"""

    type: Literal["approval"]
    decisions: list[ApprovalDecision] = Field(min_length=1)


class ResumeEvent(WireModel):
    """消费指定 Turn 的一次等待点。"""

    type: Literal["yuxi.session.input.resume"]
    turn_id: str = Field(min_length=1)
    waitpoint_id: str = Field(min_length=1)
    response: Annotated[AnswerPayload | ApprovalPayload, Field(discriminator="type")]


class CancelOptions(WireModel):
    """显式取消目标，省略时由 owning transaction 选择当前 Turn。"""

    turn_id: str | None = Field(default=None, min_length=1)
    expected_run_id: str | None = None


class CancelEvent(WireModel):
    """官方取消输入和可选业务目标。"""

    type: Literal["agent.session.input.cancel"]
    yuxi: CancelOptions = Field(default_factory=CancelOptions)


class ContinueEvent(WireModel):
    """显式恢复暂停的输入队列。"""

    type: Literal["yuxi.session.input.continue"]


class PromoteInputEvent(WireModel):
    """将待消费输入原地转为引导。"""

    type: Literal["yuxi.session.input.promote"]
    input_id: str = Field(min_length=1)


class CancelInputEvent(WireModel):
    """移除尚未领取的 Input 批次。"""

    type: Literal["yuxi.session.input.cancel_input"]
    input_id: str = Field(min_length=1)


class TreeControlEvent(WireModel):
    """用户显式停止或继续整个协作树。"""

    type: Literal["yuxi.session.tree.stop", "yuxi.session.tree.continue"]


ThreadEvent = Annotated[
    MessageEvent | ResumeEvent | CancelEvent | ContinueEvent | PromoteInputEvent | CancelInputEvent | TreeControlEvent,
    Field(discriminator="type"),
]


class SessionEventCreate(WireModel):
    """单次接收一个输入或控制事件。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "events": [
                        {
                            "type": "agent.session.input.message",
                            "input": [{"role": "user", "content": [{"type": "input_text", "text": "请继续"}]}],
                        }
                    ]
                },
                {"events": [{"type": "agent.session.input.cancel"}]},
                {
                    "events": [
                        {
                            "type": "yuxi.session.input.resume",
                            "turn_id": "<turn-id>",
                            "waitpoint_id": "<waitpoint-id>",
                            "response": {
                                "type": "answer",
                                "answers": [{"question_id": "<question-id>", "answer": "确认"}],
                            },
                        }
                    ]
                },
            ]
        },
    )

    events: list[ThreadEvent] = Field(
        min_length=1,
        max_length=1,
        description="每次只允许一个消息或控制事件；消息事件可包含多条有序用户消息。",
    )


class SessionAgent(BaseModel):
    """公开会话绑定的智能体及快照中的有效模型。"""

    id: str = Field(description="已保存 Agent 的 slug。")
    model: str | None = Field(description="会话配置快照中的有效聊天模型；创建时解析 Agent 或系统默认值。")


class EventAccepted(BaseModel):
    """仅证明事件持久接收的 Yuxi 扩展回执。"""

    object: Literal["yuxi.session.event.accepted"] = "yuxi.session.event.accepted"
    event_id: str
    session_id: str
    input_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    status: Literal["accepted"] = "accepted"


class SessionExtension(BaseModel):
    """会话展示、队列和当前工作字段，不混入接收回执。"""

    title: str | None
    project_id: str
    is_pinned: bool
    archived: bool
    parent_session_id: str | None = None
    tool_approval_mode: str | None = None
    unread: bool
    current_turn: dict[str, Any] | None = None
    queue_paused: bool = False
    queued_input_count: int = 0
    receipt: EventAccepted | None = None


class SessionResponse(BaseModel):
    """所有会话操作共用的资源模型。"""

    id: str
    object: Literal["agent.session"] = "agent.session"
    agent: SessionAgent
    created_at: int
    last_active_at: int = Field(description="创建、持久输入或执行活动的最近时间，UTC Unix 秒。")
    status: Literal["idle", "in_progress", "requires_action", "completed", "failed", "cancelled"]
    yuxi: SessionExtension


class SessionList(BaseModel):
    """按稳定会话身份分页的列表。"""

    object: Literal["list"] = "list"
    data: list[SessionResponse]
    first_id: str | None
    last_id: str | None
    has_more: bool


class PublicItem(BaseModel):
    """已登记的公开消息或函数调用，不包含内部审计。"""

    model_config = ConfigDict(extra="allow")
    id: str
    turn_id: str | None
    status: str
    yuxi: dict[str, Any] = Field(description="Run 归属、消息 ID、投影顺序及公开业务信息。")


class InputTextContent(BaseModel):
    """持久输入正文，允许多块输入合并后的文本。"""

    type: Literal["input_text"]
    text: str


class InputImageContent(BaseModel):
    """持久输入图片；接收校验由请求边界负责。"""

    type: Literal["input_image"]
    image_url: str


class OutputTextPart(BaseModel):
    """持久助手正文内容块。"""

    type: Literal["output_text"]
    text: str


class MessageItem(PublicItem):
    """公开用户输入或助手正文。"""

    type: Literal["message"]
    role: Literal["user", "assistant"]
    content: list[Annotated[InputTextContent | InputImageContent | OutputTextPart, Field(discriminator="type")]] = Field(
        description="用户含 input_text/input_image，助手含 output_text；按内容块顺序读取。"
    )
    phase: Literal["commentary", "final_answer"] | None = Field(
        description="只有 result_run_id 指向 Run 的最终输出为 final_answer；用户输入为 null。"
    )


class FunctionCallItem(PublicItem):
    """已公开的实际工具调用。"""

    type: Literal["function_call"]
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(description="完整工具参数对象，与对应 call_id 的结果关联。")


class FunctionOutputItem(PublicItem):
    """公开函数调用的工具结果。"""

    type: Literal["function_call_output"]
    call_id: str
    output: str | list[dict[str, Any]] | None = Field(description="工具结果；正在执行或无返回值时为 null。")


class ItemList(BaseModel):
    """按公开 item 身份分页的列表。"""

    object: Literal["list"] = "list"
    data: list[Annotated[MessageItem | FunctionCallItem | FunctionOutputItem, Field(discriminator="type")]]
    first_id: str | None
    last_id: str | None
    has_more: bool
    yuxi: dict[str, Any] = Field(description="runs 只包含当前页 item 引用的轻量执行段。")


class TurnExtension(BaseModel):
    """本轮执行关联、等待、取消清理与明确结果。"""

    current_run_id: str | None
    result_run_id: str | None
    waitpoint: dict[str, Any] | None
    cancellation_pending: bool = Field(description="取消意图已提交，当前轮次仍在停止和清理。")
    runs: list[dict[str, Any]]
    output: list[Annotated[MessageItem | FunctionCallItem | FunctionOutputItem, Field(discriminator="type")]] | None
    usage: dict[str, Any]


class TurnSummary(BaseModel):
    """HTTP 与 SSE 共用的 Turn 核心字段及业务扩展。"""

    id: str
    object: Literal["agent.session.turn"] = "agent.session.turn"
    session_id: str
    agent_id: str
    subagent_id: str | None = None
    status: Literal["queued", "in_progress", "requires_action", "completed", "failed", "cancelled"]
    created_at: float
    started_at: float | None
    completed_at: float | None
    error: dict[str, Any] | None
    usage: dict[str, Any] | None = None
    yuxi: dict[str, Any]


class TurnResponse(TurnSummary):
    """单轮详情包含明确输出和执行段。"""

    yuxi: TurnExtension


class TurnList(BaseModel):
    """按稳定创建顺序分页的轮次摘要。"""

    object: Literal["list"] = "list"
    data: list[TurnSummary]
    first_id: str | None
    last_id: str | None
    has_more: bool


class InputResponse(BaseModel):
    """输入的持久接收与消费归属。"""

    input_id: str
    thread_id: str = Field(description="所属 Session UUID。")
    kind: Literal["follow_up", "steer"]
    status: str
    turn_id: str | None = Field(description="排队时为空，消费后固定。")
    run_id: str | None
    received_seq: int
    messages: list[dict[str, Any]]
    attachment_file_ids: list[str]
    attachments: list[dict[str, Any]]
    received_at: datetime
    items: list[MessageItem]
    attachment_status: Literal["preparing", "ready"] = Field(description="ready 表示本次附件已写入 Workdir；preparing 时禁止消费。")
    attachment_error: str | None = Field(description="最近一次准备失败，恢复会重试同一 Input。")


class PublicError(BaseModel):
    """现有 FastAPI 错误结构。"""

    detail: str | dict[str, Any] | list[dict[str, Any]]


def input_messages_to_domain(messages: list[InputMessage] | str) -> list[AgentRunInputMessage]:
    """在 HTTP 边界校验图片并保留消息与内容块顺序。"""
    if isinstance(messages, str):
        messages = [InputMessage(role="user", content=[InputTextPart(type="input_text", text=messages)])]
    converted = []
    for message in messages:
        parts = []
        images = []
        for part in message.content:
            if isinstance(part, InputTextPart):
                parts.append({"type": "text", "text": part.text})
                continue
            if not part.image_url.startswith("data:image/") or ";base64," not in part.image_url:
                raise HTTPException(status_code=422, detail="input_image 仅支持 data:image base64 URL")
            image_content = part.image_url.split(";base64,", 1)[1]
            if not image_content:
                raise HTTPException(status_code=422, detail="input_image 内容不能为空")
            images.append(image_content)
            parts.append({"type": "image_url", "image_url": part.image_url})
        try:
            normalize_image_contents(images)
            converted.append(build_chat_input_message_from_openai_content(parts))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return converted
