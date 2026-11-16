"""登记当前用户可见的交付物。"""

from typing import Annotated

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command
from pydantic import BaseModel, Field

from yuxi.infrastructure.postgres.manager import pg_manager
from yuxi.modules.extensions.tools.builtin.file_context import resolve_file_context
from yuxi.modules.extensions.tools.registry import tool
from yuxi.modules.workspace.runtime_paths import normalize_artifact_path
from yuxi.modules.workspace.services.deliverables import save_run_deliverables
from yuxi.shared.errors import DomainError


class PresentArtifactsInput(BaseModel):
    """声明交付物文件路径。"""

    filepaths: list[str] = Field(description="需要展示给用户的文件绝对路径列表", min_length=1, max_length=100)


PRESENT_ARTIFACTS_DESCRIPTION = """
将已经生成好的结果文件展示给用户，并自动保存独立副本到「网盘 / 我的成果」。

使用场景：
1. 你已经在合适的位置写好了最终结果文件
2. 你希望前端在对话结束后显示这些结果文件卡片
3. 这些文件需要支持下载或预览

注意事项：
1. 可以传入当前 Project Workdir、User Data 或已授权 Skills 中的普通文件
2. 不要传入中间过程文件，只有真正需要给用户看的结果文件才调用
3. 可以一次传多个文件
4. 生成文档、图片、视频、音频等最终文件后必须调用；图片在对话内展示后也需要交付保存
"""


@tool(
    category="builtin",
    tags=["文件", "交付物"],
    display_name="展示交付物",
    description=PRESENT_ARTIFACTS_DESCRIPTION,
    args_schema=PresentArtifactsInput,
)
async def present_artifacts(
    filepaths: list[str],
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """保存用户成果副本后登记交付路径，失败时不伪造成功。"""
    try:
        normalized_paths = [_normalize_presented_artifact_path(filepath, runtime) for filepath in filepaths]
        if normalized_paths:
            context = runtime.context
            async with pg_manager.get_async_session_context() as db:
                await save_run_deliverables(
                    uid=str(context.uid),
                    thread_id=context.thread_id,
                    run_id=context.run_id,
                    worker_id=context.worker_id,
                    operation_id=tool_call_id,
                    paths=normalized_paths,
                    db=db,
                )
    except (DomainError, ValueError) as exc:
        return Command(update={"messages": [ToolMessage(content=f"Error: {exc}", tool_call_id=tool_call_id, status="error")]})

    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [
                ToolMessage(
                    content="已将交付物展示给用户，并保存到我的成果" if normalized_paths else "没有需要交付的文件",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def _normalize_presented_artifact_path(filepath: str, runtime: ToolRuntime) -> str:
    """校验交付物属于当前用户可见的普通文件。"""
    from yuxi.modules.agents.runtime.sandbox.backend import UserSandboxBackend

    runtime_context = runtime.context
    uid, workdir_relative_path = resolve_file_context(runtime)

    normalized_path = normalize_artifact_path(getattr(runtime_context, "workdir_path", None), filepath)
    backend = UserSandboxBackend(
        uid=str(uid),
        workdir_path=workdir_relative_path,
    )
    if not backend.regular_file_exists(normalized_path):
        raise ValueError(f"文件不存在或不是普通文件: {filepath}")
    return normalized_path
