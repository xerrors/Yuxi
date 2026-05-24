"""思维导图工具函数。"""

import json
import textwrap
from typing import Any

MINDMAP_SYSTEM_PROMPT = """你是一个专业的知识整理助手。

你的任务是分析用户提供的文件列表，生成一个层次分明的思维导图结构。

**核心规则：每个文件名只能出现一次！不允许重复！**

要求：
1. 思维导图要有清晰的层级结构（2-4层）
2. 根节点是知识库名称
3. 第一层是主要分类（如：技术文档、规章制度、数据资源等）
4. 第二层是子分类
5. **叶子节点必须是具体的文件名称**
6. **每个文件名在整个思维导图中只能出现一次，不得重复！**
7. 如果一个文件可能属于多个分类，只选择最合适的一个分类放置
8. 使用合适的emoji图标增强可读性
9. 返回JSON格式，遵循以下结构：

```json
{
  "content": "知识库名称",
  "children": [
    {
      "content": "🎯 主分类1",
      "children": [
        {
          "content": "子分类1.1",
          "children": [
            {"content": "文件名1.txt", "children": []},
            {"content": "文件名2.pdf", "children": []}
          ]
        }
      ]
    },
    {
      "content": "💻 主分类2",
      "children": [
        {"content": "文件名3.docx", "children": []},
        {"content": "文件名4.md", "children": []}
      ]
    }
  ]
}
```

**重要约束：**
- 每个文件名在整个JSON中只能出现一次
- 不要按多个维度分类导致文件重复
- 选择最主要、最合适的分类维度
- 每个叶子节点的children必须是空数组[]
- 分类名称要简洁明了
- 使用emoji增强视觉效果
"""


def build_database_file_list(files: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "file_id": file_id,
            "filename": file_info.get("filename", ""),
            "type": file_info.get("type", ""),
            "status": file_info.get("status", ""),
            "created_at": file_info.get("created_at", ""),
        }
        for file_id, file_info in files.items()
    ]


def collect_mindmap_files(all_files: dict[str, dict[str, Any]], file_ids: list[str]) -> list[dict[str, str]]:
    return [
        {
            "filename": all_files[file_id].get("filename", ""),
            "type": all_files[file_id].get("type", ""),
        }
        for file_id in file_ids
        if file_id in all_files
    ]


def build_mindmap_user_message(db_name: str, files_info: list[dict[str, str]], user_prompt: str = "") -> str:
    files_text = "\n".join([f"- {file_info['filename']} ({file_info['type']})" for file_info in files_info])
    return textwrap.dedent(f"""请为知识库\"{db_name}\"生成思维导图结构。

        文件列表（共{len(files_info)}个文件）：
        {files_text}

        {f"用户补充说明：{user_prompt}" if user_prompt else ""}

        **重要提醒：**
        1. 这个知识库共有{len(files_info)}个文件
        2. 每个文件名只能在思维导图中出现一次
        3. 不要让同一个文件出现在多个分类下
        4. 为每个文件选择最合适的唯一分类

        请生成合理的思维导图结构。""")


def parse_mindmap_content(content: str) -> dict[str, Any]:
    if "```json" in content:
        json_start = content.find("```json") + 7
        json_end = content.find("```", json_start)
        content = content[json_start:json_end].strip()
    elif "```" in content:
        json_start = content.find("```") + 3
        json_end = content.find("```", json_start)
        content = content[json_start:json_end].strip()

    mindmap_data = json.loads(content)
    if not isinstance(mindmap_data, dict) or "content" not in mindmap_data:
        raise ValueError("思维导图结构不正确")
    return mindmap_data
