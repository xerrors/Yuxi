"""知识库示例问题生成工具。"""

import json
import textwrap
from typing import Any

from fastapi import HTTPException

from yuxi import config, knowledge_base
from yuxi.knowledge.factory import KnowledgeBaseFactory
from yuxi.models import select_model
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.utils import logger

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


def build_sample_question_file_list(files: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "filename": file_info.get("filename", ""),
            "type": file_info.get("type") or file_info.get("file_type", ""),
        }
        for file_info in files.values()
    ]


def build_sample_questions_user_message(db_name: str, files_info: list[dict[str, str]], count: int) -> str:
    files_text = "\n".join([f"- {file_info['filename']} ({file_info['type']})" for file_info in files_info[:20]])
    file_count_text = f"（共{len(files_info)}个文件）" if len(files_info) > 20 else ""

    return textwrap.dedent(f"""请为知识库\"{db_name}\"生成{count}个测试问题。

        知识库文件列表{file_count_text}：
        {files_text}

        请根据这些文件的名称和类型，生成{count}个有价值的测试问题。""")


def parse_sample_questions_content(content: str) -> list[str]:
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


async def generate_database_sample_questions(kb_id: str, count: int = 10) -> dict[str, Any]:
    db_info = await knowledge_base.get_database_info(kb_id, include_files=True)
    if not db_info:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    kb_type = (db_info.get("kb_type") or "").lower()
    if not KnowledgeBaseFactory.get_kb_class(kb_type).supports_documents:
        raise HTTPException(status_code=400, detail=f"{db_info.get('name') or kb_type} 不支持基于文件生成测试问题")

    db_name = db_info.get("name", "")
    all_files = db_info.get("files", {})
    if not all_files:
        raise HTTPException(status_code=400, detail="知识库中没有文件")

    files_info = build_sample_question_file_list(all_files)
    logger.info(f"开始生成知识库问题，知识库: {db_name}, 文件数量: {len(files_info)}, 问题数量: {count}")

    model = select_model(model_spec=config.default_model)
    messages = [
        {"role": "system", "content": SAMPLE_QUESTIONS_SYSTEM_PROMPT},
        {"role": "user", "content": build_sample_questions_user_message(db_name, files_info, count)},
    ]
    response = await model.call(messages, stream=False)
    content = response.content if hasattr(response, "content") else str(response)

    try:
        questions = parse_sample_questions_content(content)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"AI返回的JSON解析失败: {e}, 原始内容: {content}")
        raise HTTPException(status_code=500, detail=f"AI返回格式错误: {str(e)}") from e

    logger.info(f"成功生成{len(questions)}个问题")

    try:
        await KnowledgeBaseRepository().update(kb_id, {"sample_questions": questions})
        logger.info(f"成功保存 {len(questions)} 个问题到知识库 {kb_id}")
    except Exception as save_error:
        logger.error(f"保存问题失败: {save_error}")

    return {
        "message": "success",
        "questions": questions,
        "count": len(questions),
        "kb_id": kb_id,
        "db_name": db_name,
    }


async def get_database_sample_questions(kb_id: str) -> dict[str, Any]:
    kb = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    questions = kb.sample_questions or []
    return {
        "message": "success",
        "questions": questions,
        "count": len(questions),
        "kb_id": kb_id,
    }
