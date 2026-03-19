import os
import traceback
import uuid
from typing import Annotated, Any

import requests
from langgraph.types import interrupt

from yuxi import config, graph_base
from yuxi.agents.common.toolkits.registry import ToolExtraMetadata, _all_tool_instances, _extra_registry, tool
from yuxi.storage.minio import aupload_file_to_minio
from yuxi.utils import logger
from yuxi.utils.question_utils import normalize_questions

# Lazy initialization for TavilySearch (only when API key is available)
_tavily_search_instance = None


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


@tool(category="buildin", tags=["图片", "生成"], display_name="Qwen-Image")
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
