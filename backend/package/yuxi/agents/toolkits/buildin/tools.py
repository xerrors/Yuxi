import os
import time
import traceback
import uuid
from pathlib import Path
from typing import Annotated, Any

import requests

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from yuxi import config, graph_base
from yuxi.agents.toolkits.registry import ToolExtraMetadata, _all_tool_instances, _extra_registry, tool
from yuxi.storage.minio import aupload_file_to_minio
from yuxi.utils import logger
from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS
from yuxi.utils.question_utils import normalize_questions

# Lazy initialization for TavilySearch (only when API key is available)
_tavily_search_instance = None

# Lazy initialization for SearXNG Search (only when SEARXNG_URL is configured)
_searxng_search_instance = None

QWEN_IMAGE_CONFIG_GUIDE = """
使用前需要先配置硅基流动的图片生成访问凭证。

请在后端运行环境中配置环境变量：
- `SILICONFLOW_API_KEY`：用于调用 SiliconFlow 的图片生成接口

配置完成后即可使用该工具生成图片。
""".strip()


def _create_tavily_search():
    """Create and register TavilySearch tool with metadata."""
    global _tavily_search_instance
    if _tavily_search_instance is None:
        from langchain_tavily import TavilySearch

        _tavily_search_instance = TavilySearch()

    return _tavily_search_instance


# 注册 TavilySearch 工具（延迟初始化）
def _register_tavily_tool():
    """Register TavilySearch tool with extra metadata."""
    tavily_instance = _create_tavily_search()
    # 手动注册到全局注册表
    _extra_registry["tavily_search"] = ToolExtraMetadata(
        category="buildin",
        tags=["搜索"],
        display_name="Tavily 网页搜索",
    )
    # 添加到工具实例列表
    _all_tool_instances.append(tavily_instance)


# 模块加载时注册
if config.enable_web_search:
    try:
        _register_tavily_tool()
    except Exception as e:
        logger.warning(f"Failed to register TavilySearch tool: {e}")

# =============================================================================
# SearXNG 搜索工具

# 定义 SearXNG 可用的备用引擎列表（按优先级排序，无需 API Key）
# 参考: https://docs.searxng.org/admin/engines/configured_engines.html
# 重要：每个引擎独立限流，多引擎轮询可以有效分散请求
_SEARXNG_ENGINE_PRIORITIES = [
    # 与 docker/volumes/searxng/settings.yml 中的 keep_only 保持一致
    ["google"],
    ["bing"],
    ["duckduckgo"],
]


def _is_rate_limit_error(exception: Exception) -> bool:
    """判断异常是否来自第三方引擎的限流（429）或服务不可用（503）。"""
    msg = str(exception).lower()
    return any(keyword in msg for keyword in ["429", "503", "rate", "limit", "too many", "unavailable", "temporarily"])


def _is_searxng_unreachable(exception: Exception) -> bool:
    """判断异常是否来自 SearXNG 服务本身不可用（连接失败、超时）。"""
    msg = str(exception).lower()
    return any(
        keyword in msg
        for keyword in ["connection", "timeout", "refused", "network", "resolve", "unable to connect"]
    )


def _get_searxng_wrapper():
    """获取或创建 SearxSearchWrapper 实例（延迟初始化）。"""
    if not hasattr(_get_searxng_wrapper, "_wrapper"):
        from langchain_community.utilities import SearxSearchWrapper

        _get_searxng_wrapper._wrapper = SearxSearchWrapper(searx_host=config.searxng_url)
    return _get_searxng_wrapper._wrapper


# 引擎轮询索引（模块级别持久化，每次工具调用后递增，实现引擎级别的负载分散）
_engine_idx = {"current": 0}


def _get_next_engine_config():
    """获取下一个引擎配置，逐级降级。"""
    global _engine_idx
    idx = _engine_idx["current"]
    _engine_idx["current"] = (idx + 1) % len(_SEARXNG_ENGINE_PRIORITIES)
    return _SEARXNG_ENGINE_PRIORITIES[idx]


def _call_searxng_with_retry(query: str, engines: list[str] | None = None, num_results: int = 7, attempt: int = 0) -> list[dict]:
    """Execute search via direct HTTP API (bypassing LangChain wrapper to preserve all fields including score).

    Falls back to LangChain wrapper only when the direct API is unavailable.
    """
    params = {"q": query, "format": "json", "limit": num_results}
    if engines:
        params["engines"] = engines

    try:
        resp = requests.get(f"{config.searxng_url}/search", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except Exception as exc:
        if not _is_rate_limit_error(exc) and not _is_searxng_unreachable(exc):
            raise

        if attempt >= 2:
            fallback_engines = _get_next_engine_config()
            logger.warning(
                f"SearXNG direct API retry #{attempt + 1} failed, "
                f"switching to fallback engines={fallback_engines}: {exc}"
            )
            fallback_params = {"q": query, "format": "json", "limit": num_results}
            if fallback_engines:
                fallback_params["engines"] = fallback_engines
            try:
                resp = requests.get(f"{config.searxng_url}/search", params=fallback_params, timeout=15)
                resp.raise_for_status()
                return resp.json().get("results", [])
            except Exception as fallback_exc:
                logger.error(f"SearXNG fallback engines also failed: {fallback_exc}")
                raise fallback_exc from exc

        wait_seconds = min(2**attempt * 1.5, 8.0)
        logger.warning(
            f"SearXNG search hit rate limit (attempt {attempt + 1}/3), "
            f"retrying in {wait_seconds:.1f}s: {exc}"
        )
        time.sleep(wait_seconds)
        return _call_searxng_with_retry(query, engines, num_results, attempt + 1)


@tool(
    name_or_callable="searxng_search",
    description="使用 SearXNG 元搜索引擎进行网页搜索。当第三方引擎（如 DuckDuckGo）偶发限流时，会自动重试（最多3次，每次间隔1-8秒），并在必要时切换到备用引擎（Qwant/Startpage/Mojeek等）。返回标准化格式的搜索结果。",
    tags=["搜索"],
    display_name="SearXNG 网页搜索",
)
def searxng_search_func(query: Annotated[str, "搜索查询关键词"]) -> dict:
    """Execute SearXNG search with automatic retry and engine fallback.

    Returns a dict compatible with the Tavily format consumed by the frontend:
      - query: str          — 原始查询
      - results[].url       — 网页链接（link → url 映射）
      - results[].title     — 网页标题
      - results[].content  — 摘要（snippet → content 映射）
      - results[].score    — 相关度分（来自 SearXNG 的相对评分，1.0 最高，按序递减）
      - results[].published_date — 发布日期（SearXNG 可能为空）
    """
    global _searxng_search_instance

    engines = _get_next_engine_config()

    try:
        raw_results = _call_searxng_with_retry(query, engines if engines else None, num_results=7)
    except Exception as exc:
        logger.error(f"SearXNG search failed after all retries: {exc}")
        return {
            "query": query,
            "results": [],
            "error": f"搜索服务暂时不可用（{type(exc).__name__}），"
            f"建议稍后重试或检查 SearXNG 容器状态（docker compose --profile search ps）。",
        }

    results = []
    for r in raw_results:
        if isinstance(r, dict) and r.get("Result") == "No good Search Result was found":
            continue
        results.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url") or r.get("link", ""),
                "content": r.get("content") or r.get("snippet", ""),
                "score": r.get("score", 1.0),
                "published_date": r.get("publishedDate") or r.get("published_date", ""),
            }
        )

    return {
        "query": query,
        "results": results,
    }
    """判断异常是否来自第三方引擎的限流（429）或服务不可用（503）。"""
    msg = str(exception).lower()
    return any(keyword in msg for keyword in ["429", "503", "rate", "limit", "too many", "unavailable", "temporarily"])


def _is_searxng_unreachable(exception: Exception) -> bool:
    """判断异常是否来自 SearXNG 服务本身不可用（连接失败、超时）。"""
    msg = str(exception).lower()
    return any(
        keyword in msg
        for keyword in ["connection", "timeout", "refused", "network", "resolve", "unable to connect"]
    )


# 注册 SearXNG 扩展元数据（@tool 装饰器已在模块加载时完成工具注册）
if config.enable_searxng_search and "searxng_search" not in _extra_registry:
    try:
        _extra_registry["searxng_search"] = ToolExtraMetadata(
            category="buildin",
            tags=["搜索"],
            display_name="SearXNG 网页搜索",
        )
    except Exception as e:
        logger.warning(f"Failed to register SearXNG extra metadata: {e}")


class PresentArtifactsInput(BaseModel):
    """Expose artifact files to the frontend after the agent finishes."""

    filepaths: list[str] = Field(description=f"需要展示给用户的文件绝对路径列表，只允许位于 {VIRTUAL_PATH_OUTPUTS} 下")


def _normalize_presented_artifact_path(filepath: str, runtime: ToolRuntime) -> str:
    from yuxi.agents.backends.sandbox.paths import (
        VIRTUAL_PATH_PREFIX,
        ensure_thread_dirs,
        resolve_virtual_path,
        sandbox_outputs_dir,
    )

    outputs_virtual_prefix = f"{VIRTUAL_PATH_PREFIX}/outputs"
    runtime_context = runtime.context
    thread_id = getattr(runtime_context, "thread_id", None)
    if not thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    user_id = getattr(runtime_context, "user_id", None)
    if not user_id:
        raise ValueError("当前运行时缺少 user_id")

    ensure_thread_dirs(thread_id, str(user_id))
    outputs_dir = sandbox_outputs_dir(thread_id).resolve()
    normalized_input = str(filepath or "").strip()
    if not normalized_input:
        raise ValueError("文件路径不能为空")

    stripped = normalized_input.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    if stripped == virtual_prefix or stripped.startswith(f"{virtual_prefix}/"):
        actual_path = resolve_virtual_path(thread_id, normalized_input, user_id=str(user_id))
    else:
        actual_path = Path(normalized_input).expanduser().resolve()

    if not actual_path.exists() or not actual_path.is_file():
        raise ValueError(f"文件不存在或不是普通文件: {normalized_input}")

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"只允许展示 {outputs_virtual_prefix}/ 下的文件: {normalized_input}") from exc

    return f"{outputs_virtual_prefix}/{relative_path.as_posix()}"


@tool(category="buildin", tags=["计算"], display_name="计算器")
def calculator(a: float, b: float, operation: str) -> float:
    """计算器：对给定的2个数字进行基本数学运算"""
    try:
        if operation == "add":
            return a + b
        elif operation == "subtract":
            return a - b
        elif operation == "multiply":
            return a * b
        elif operation == "divide":
            if b == 0:
                raise ZeroDivisionError("除数不能为零")
            return a / b
        else:
            raise ValueError(f"不支持的运算类型: {operation}，仅支持 add, subtract, multiply, divide")
    except Exception as e:
        logger.error(f"Calculator error: {e}")
        raise


PRESENT_ARTIFACTS_DESCRIPTION = f"""
将已经生成好的结果文件展示给用户。

使用场景：
1. 你已经在 `{VIRTUAL_PATH_OUTPUTS}` 下写好了最终结果文件
2. 你希望前端在对话结束后显示这些结果文件卡片
3. 这些文件需要支持下载或预览

注意事项：
1. 只能传入 `{VIRTUAL_PATH_OUTPUTS}` 下的文件
2. 不要传入中间过程文件，只有真正需要给用户看的结果文件才调用
3. 可以一次传多个文件
"""


@tool(
    category="buildin",
    tags=["文件", "交付物"],
    display_name="展示交付物",
    description=PRESENT_ARTIFACTS_DESCRIPTION,
    args_schema=PresentArtifactsInput,
)
def present_artifacts(
    filepaths: list[str],
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """登记当前线程 outputs 目录下的交付物文件，使前端在对话结束后展示给用户。"""
    try:
        normalized_paths = [_normalize_presented_artifact_path(filepath, runtime) for filepath in filepaths]
    except ValueError as exc:
        return Command(update={"messages": [ToolMessage(content=f"Error: {exc}", tool_call_id=tool_call_id)]})

    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [ToolMessage(content="已将交付物展示给用户", tool_call_id=tool_call_id)],
        }
    )


ASK_USER_QUESTION_DESCRIPTION = """
在执行过程中，当你需要用户做决定或补充需求时，使用这个工具向用户提问。

适用场景：
1. 收集用户偏好或需求（例如风格、范围、优先级）
2. 澄清模糊指令（存在多种合理解释时）
3. 在实现过程中让用户选择方案方向
4. 在有明显权衡时让用户做取舍

使用规范：
1. questions 提供 1-5 个问题，每项包含：question、options、multi_select、allow_other
2. 每个问题的 options 提供 2-5 个有区分度的选项，每项包含 label 和 value
3. 若有推荐选项：把推荐项放在第一位，并在 label 末尾加 "(Recommended)"
4. 若需要多选：将该问题的 multi_select 设为 true
5. allow_other 通常保持 true，用户可通过 Other 输入自定义答案

注意事项：
1. 不要用这个工具询问“是否继续执行”“计划是否准备好”这类流程控制问题
2. 不要在信息已充分、无需用户决策时滥用该工具
3. 先基于现有上下文自行决策，只有关键不确定性时才提问

返回结果：
answer 为 object，格式为 {question_id: answer}。
其中 answer 可能是 string（单选）、list（多选）或 object（Other 文本）。
"""


@tool(
    category="buildin",
    tags=["交互"],
    display_name="向用户提问",
    description=ASK_USER_QUESTION_DESCRIPTION,
)
def ask_user_question(
    questions: Annotated[
        list[dict] | str | None,
        "问题列表，每项格式 {question, options, multi_select, allow_other, question_id(optional)}",
    ] = None,
    question: Annotated[str, "兼容字段：单个问题文本（建议优先使用 questions）"] = "",
    options: Annotated[list[dict] | str | None, "兼容字段：单个问题候选项（建议优先使用 questions）"] = None,
    multi_select: Annotated[bool, "兼容字段：单个问题是否允许多选"] = False,
    allow_other: Annotated[bool, "兼容字段：单个问题是否允许 Other 自定义答案"] = True,
) -> dict:
    """向用户发起问题并等待回答。"""
    # 解析 options 参数：如果是字符串，尝试解析为 JSON
    if isinstance(options, str):
        try:
            import json

            options = json.loads(options)
            logger.debug(f"Parsed string options to list: {options}")
        except Exception as e:
            logger.error(f"Failed to parse options string: {e}, using empty list")
            options = []

    # 解析 questions 参数：如果是字符串，尝试解析为 JSON
    if isinstance(questions, str):
        try:
            import json

            questions = json.loads(questions)
            logger.debug(f"Parsed string questions to list: {questions}")
        except Exception as e:
            logger.error(f"Failed to parse questions string: {e}, using None")
            questions = None

    input_questions = questions
    if not input_questions:
        legacy_question = str(question or "").strip()
        if legacy_question:
            input_questions = [
                {
                    "question": legacy_question,
                    "options": options or [],
                    "multi_select": multi_select,
                    "allow_other": allow_other,
                }
            ]

    normalized_questions = normalize_questions(input_questions or [])

    if not normalized_questions:
        raise ValueError("questions 至少需要包含一个有效问题")

    interrupt_payload = {
        "questions": normalized_questions,
        "source": "ask_user_question",
    }
    answer = interrupt(interrupt_payload)

    return {
        "questions": normalized_questions,
        "answer": answer,
    }


KG_QUERY_DESCRIPTION = """
使用这个工具可以查询知识图谱中包含的三元组信息。
关键词（query），使用可能帮助回答这个问题的关键词进行查询，不要直接使用用户的原始输入去查询。
"""


@tool(category="buildin", tags=["图谱"], display_name="查询知识图谱", description=KG_QUERY_DESCRIPTION)
def query_knowledge_graph(query: Annotated[str, "The keyword to query knowledge graph."]) -> Any:
    """使用这个工具可以查询知识图谱中包含的三元组信息。关键词（query），使用可能帮助回答这个问题的关键词进行查询，不要直接使用用户的原始输入去查询。"""
    try:
        logger.debug(f"Querying knowledge graph with: {query}")
        result = graph_base.query_node(query, hops=2, return_format="triples")
        logger.debug(
            f"Knowledge graph query returned "
            f"{len(result.get('triples', [])) if isinstance(result, dict) else 'N/A'} triples"
        )
        return result
    except Exception as e:
        logger.error(f"Knowledge graph query error: {e}, {traceback.format_exc()}")
        return f"知识图谱查询失败: {str(e)}"


@tool(
    category="buildin",
    tags=["图片", "生成"],
    display_name="Qwen-Image",
    config_guide=QWEN_IMAGE_CONFIG_GUIDE,
)
async def text_to_img_qwen_image(
    prompt: Annotated[str, "用于生成图片的文本描述"],
    negative_prompt: Annotated[str, "负面提示词，用于指定不想出现在图片中的元素"] = "",
    num_inference_steps: Annotated[int, "推理步数，范围1-100"] = 20,
    guidance_scale: Annotated[float, "引导强度，控制图片与提示词的匹配程度"] = 7.5,
    user_id: Annotated[str, "用户ID，用于图片归档路径"] = "unknown",
) -> str:
    """使用 Qwen-Image 模型生成图片，返回图片的URL，需要注意的是，生成结果不会默认展示，需要将返回的URL进行展示处理。"""
    url = "https://api.siliconflow.cn/v1/images/generations"

    payload = {
        "model": "Qwen/Qwen-Image",
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "num_inference_steps": num_inference_steps,
        "guidance_scale": guidance_scale,
    }
    headers = {"Authorization": f"Bearer {os.getenv('SILICONFLOW_API_KEY')}", "Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers)
        response_json = response.json()
    except Exception as e:
        logger.error(f"Failed to generate image with: {e}")
        raise ValueError(f"Image generation failed: {e}")

    try:
        image_url = response_json["images"][0]["url"]
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Failed to parse image URL from response: {e}, {response_json=}")
        raise ValueError(f"Image URL extraction failed: {e}")

    # Upload to MinIO
    response = requests.get(image_url)
    file_data = response.content

    safe_user_id = str(user_id or "unknown").replace("/", "_").replace("\\", "_")
    file_name = f"user/{safe_user_id}/generated-images/{uuid.uuid4()}.jpg"
    image_url = await aupload_file_to_minio(bucket_name="public", file_name=file_name, data=file_data)
    logger.info(f"Image uploaded. URL: {image_url}")
    return image_url
