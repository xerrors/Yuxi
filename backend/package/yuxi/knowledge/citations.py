"""知识库检索结果的引用编号。

把"智能体自己写来源字符串"改成"按检索顺序编号"，让回答里的引用可以被确定性解析。
这是原 `<cite source="$SOURCE">` 约定失效的根因：模型需要凭记忆写出文件名，既不稳定也无法校验。
"""

from __future__ import annotations

import json
from typing import Any

# 单个来源名称在提示中的最大长度，避免长文件名挤占上下文
CITATION_SOURCE_MAX_CHARS = 60


KB_CITE_TOOL_NAME = "query_kb"


def build_citation_entries(results: list[Any], offset: int = 0) -> list[dict[str, Any]]:
    """把检索结果按返回顺序转成引用条目。

    offset 是本次检索之前已经用掉的编号数量，用于让编号在一次会话内连续递增，
    避免多轮检索时同一个编号指向不同片段。跳过非字典结果后连续编号，
    保证编号与最终输出给模型的片段一一对应。
    """
    entries: list[dict[str, Any]] = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        source = str(metadata.get("source") or "").strip()
        entries.append(
            {
                "index": offset + len(entries) + 1,
                "kb_id": str(result.get("kb_id") or ""),
                "file_id": str(result.get("file_id") or metadata.get("file_id") or ""),
                "chunk_id": str(result.get("id") or metadata.get("chunk_id") or ""),
                "source": source[:CITATION_SOURCE_MAX_CHARS] or "未知来源",
            }
        )
    return entries


def count_prior_citations(messages: Any) -> int:
    """统计本次检索之前已经发出的引用编号数量。

    只依赖已落盘的工具消息，不引入任何运行时状态或持久化。历史消息无法解析时
    按 0 处理——此时退化成"每次检索各自从 1 编号"，与改造前行为一致，不会更糟。
    """
    # 两个口径取较大值：条目计数覆盖完整历史，已写入的最大编号则能在历史被
    # 上下文压缩截断时兜住，避免新检索复用之前已经发过的编号。
    total = 0
    max_cite = 0
    for message in messages or []:
        if getattr(message, "name", None) != KB_CITE_TOOL_NAME:
            continue
        payload = _tool_message_payload(getattr(message, "content", None))
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, dict):
                continue
            total += 1
            max_cite = max(max_cite, _as_positive_int(item.get("cite")))
    return max(total, max_cite)


def _as_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _tool_message_payload(content: Any) -> Any:
    """工具消息的 content 可能是对象，也可能是序列化后的字符串。"""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return None


def render_citation_hint(entries: list[dict[str, Any]]) -> str:
    """生成本次检索的可用引用清单，供智能体按编号引用。"""
    if not entries:
        return ""

    listing = "\n".join(f"[{entry['index']}] {entry['source']}" for entry in entries)
    return (
        "引用要求：上面片段的编号是本次会话内唯一的，之前的检索已经用掉了更小的编号。"
        '回答中每个来自这些片段的论断，句末附加 <cite type="file">编号</cite>，编号必须是上面列出的值。'
        "不要自己写来源名称，不要引用上面没有的编号，也不要复用更早检索里的编号。\n"
        f"{listing}"
    )
