"""知识库示例问题生成工具。"""

import json
import textwrap
from typing import Any

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.knowledge.factory import KnowledgeBaseFactory
from yuxi.modules.knowledge.repositories.bases import KnowledgeBaseRepository
from yuxi.modules.knowledge.services import bases as knowledge_bases
from yuxi.modules.models import select_model
from yuxi.modules.system.options import system_options
from yuxi.shared.errors import InvalidOperationError, OperationFailedError, ResourceNotFoundError

SAMPLE_QUESTIONS_SYSTEM_PROMPT = """你是一个专业的知识库问答测试专家。

你的任务是根据知识库中的文件列表，生成有价值的测试问题。

要求：
1. 问题要具体、有针对性，基于文件名称和类型推测可能的内容
2. 问题要涵盖不同方面和难度
3. 问题要简洁明了，适合用于检索测试
4. 问题要多样化，包括事实查询、概念解释、操作指导等
5. 问题长度控制在10-30字之间
6. 直接返回JSON数组格式，不要其他说明

返回格式：
```json
{
  "questions": [
    "问题1？",
    "问题2？",
    "问题3？"
  ]
}
```
"""


async def generate_knowledge_base_sample_questions(kb_id: str, count: int = 10) -> dict[str, Any]:
    """按知识库文件清单生成示例问题并保存。"""
    kb_info = await knowledge_bases.get_knowledge_base_info(kb_id, include_files=True)
    if not kb_info:
        raise ResourceNotFoundError(f"知识库 {kb_id} 不存在")

    kb_type = kb_info.kb_type.lower()
    if not KnowledgeBaseFactory.get_kb_class(kb_type).supports_documents:
        raise InvalidOperationError(f"{kb_info.name or kb_type} 不支持基于文件生成测试问题")

    name = kb_info.name
    all_files = kb_info.files or {}
    if not all_files:
        raise InvalidOperationError("知识库中没有文件")

    files_info = build_sample_question_file_list(all_files)
    logger.info(f"开始生成知识库问题，知识库: {name}, 文件数量: {len(files_info)}, 问题数量: {count}")

    model = select_model(model_spec=(await system_options.get())["default_model"])
    messages = [
        {"role": "system", "content": SAMPLE_QUESTIONS_SYSTEM_PROMPT},
        {"role": "user", "content": build_sample_questions_user_message(name, files_info, count)},
    ]
    response = await model.call(messages, stream=False)
    content = response.content if hasattr(response, "content") else str(response)

    try:
        questions = parse_sample_questions_content(content)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"AI返回的JSON解析失败: {e}, 原始内容: {content}")
        raise OperationFailedError(f"AI返回格式错误: {str(e)}") from e

    logger.info(f"成功生成{len(questions)}个问题")

    saved = await KnowledgeBaseRepository().update(kb_id, {"sample_questions": questions})
    if saved is None:
        raise ResourceNotFoundError(f"知识库 {kb_id} 不存在")
    logger.info(f"成功保存 {len(questions)} 个问题到知识库 {kb_id}")

    return {
        "message": "success",
        "questions": questions,
        "count": len(questions),
        "kb_id": kb_id,
        "name": name,
    }


async def get_knowledge_base_sample_questions(kb_id: str) -> dict[str, Any]:
    """读取知识库已保存的示例问题。"""
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise ResourceNotFoundError(f"知识库 {kb_id} 不存在")

    questions = kb.sample_questions or []
    return {
        "message": "success",
        "questions": questions,
        "count": len(questions),
        "kb_id": kb_id,
    }


def build_sample_question_file_list(files: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """把文件映射收敛为文件名/类型清单。"""
    return [
        {
            "filename": file_info.get("filename", ""),
            "type": file_info.get("type") or file_info.get("file_type", ""),
        }
        for file_info in files.values()
    ]


def build_sample_questions_user_message(name: str, files_info: list[dict[str, str]], count: int) -> str:
    """构造携带文件列表（最多展示 20 条）的生成请求消息。"""
    files_text = "\n".join([f"- {file_info['filename']} ({file_info['type']})" for file_info in files_info[:20]])
    file_count_text = f"（共{len(files_info)}个文件）" if len(files_info) > 20 else ""

    return textwrap.dedent(f"""请为知识库\"{name}\"生成{count}个测试问题。

        知识库文件列表{file_count_text}：
        {files_text}

        请根据这些文件的名称和类型，生成{count}个有价值的测试问题。""")


def parse_sample_questions_content(content: str) -> list[str]:
    """解析模型返回内容（可带代码围栏）中的问题列表。"""
    if "```json" in content:
        json_start = content.find("```json") + 7
        json_end = content.find("```", json_start)
        if json_end == -1:
            raise ValueError("AI返回的JSON代码块不完整")
        content = content[json_start:json_end].strip()
    elif "```" in content:
        json_start = content.find("```") + 3
        json_end = content.find("```", json_start)
        if json_end == -1:
            raise ValueError("AI返回的代码块不完整")
        content = content[json_start:json_end].strip()

    questions_data = json.loads(content)
    questions = questions_data.get("questions", []) if isinstance(questions_data, dict) else []
    if not questions or not isinstance(questions, list):
        raise ValueError("AI返回的问题格式不正确")
    return questions
