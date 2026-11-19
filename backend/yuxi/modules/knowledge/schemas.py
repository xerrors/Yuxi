"""知识库只读工具的 Pydantic 契约，按 list → query → search_file → find → open 的使用顺序组织。

被 MCP 工具（mcp/server.py）与 Public API（public_v1/knowledge.py）共用；字段、默认值、
校验约束与类 docstring 都会随 model_json_schema() 对外发布，修改任一项都是契约变更。
业务性约束（patterns 非空、kb_id/kb_name/query 三选一等）由服务层校验，这里只定义结构与取值范围。
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from yuxi.modules.knowledge.metadata import MetadataFilter


class ListInputSchema(BaseModel):
    """可选读取文档能力供平台资源选择与引用使用。"""

    include_capabilities: bool = False


class SearchInputSchema(BaseModel):
    # query_kb 的输入：按关键词检索指定知识库的内容片段
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    query_text: str = Field(description="检索关键词，应提炼为有助于召回答案的关键词或短语")
    file_name: str | None = Field(default=None, description="可选文件名关键词过滤，非必要不要使用")


class MetadataInputSchema(BaseModel):
    """读取当前知识库的属性定义，不获取其他知识库字段。"""

    model_config = ConfigDict(extra="forbid")

    kb_id: str = Field(description="来自 list_kbs 的知识库 ID")


class MetadataSearchInputSchema(SearchInputSchema):
    """MCP 的属性过滤契约，Public HTTP 继续使用原输入模型。"""

    model_config = ConfigDict(extra="forbid")

    filters: MetadataFilter | Annotated[list[Any], Field(max_length=0)] | None = Field(
        default=None,
        description=(
            "可选结构化属性条件；field 使用 get_kb_metadata 返回的 key，and/or 分组通过 conditions 组合。"
            "省略、null 或空数组表示无属性筛选；缺失值不匹配普通比较，零候选不放宽范围。"
        ),
    )


class SearchResultSchema(BaseModel):
    # query_kb 返回的单个命中片段
    id: str = Field(description="检索结果 ID，通常对应 chunk_id")
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(default="", description="结果所属文件 ID，可用于 Find/Open")
    content: str = Field(description="chunk 内容")
    metadata: dict[str, Any] = Field(default_factory=dict, description="来源、分数、chunk_index 等附加信息")


class SearchOutputSchema(BaseModel):
    # query_kb 的结构化输出
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    results: list[SearchResultSchema] = Field(default_factory=list, description="检索结果列表")


class SearchFileInput(BaseModel):
    """Agent 与 Public API 共用的文件搜索条件。"""

    kb_id: str | None = Field(default=None, description="可选知识库资源 ID；指定后只搜索该知识库")
    kb_name: str | None = Field(default=None, description="可选知识库名称；与 ID 同时指定时需同时匹配")
    query: str | None = Field(default=None, description="文件名关键词；指定知识库时可留空以列出文件")
    offset: int = Field(default=0, ge=0, description="偏移量，从 0 开始")
    limit: int = Field(default=300, ge=1, le=5000, description="返回数量限制，默认 300")


class FindInputSchema(BaseModel):
    # find_kb_document 的输入：patterns 的"至少一个"由服务层在过滤空模式后判定
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="要检索的文件 ID")
    patterns: list[str] = Field(description="关键词或正则模式列表，至少提供一个")
    use_regex: bool = Field(default=False, description="是否将 patterns 作为正则表达式处理")
    case_sensitive: bool = Field(default=False, description="是否区分大小写")
    max_windows: int = Field(default=5, ge=1, le=20, description="最多返回的上下文窗口数量")
    window_size: int = Field(default=80, ge=1, le=200, description="每个上下文窗口的行数")


class FindWindowSchema(BaseModel):
    # find_kb_document 返回的单个上下文窗口
    start_line: int = Field(description="窗口起始行号，1-based")
    end_line: int = Field(description="窗口结束行号，1-based")
    matched_lines: list[int] = Field(default_factory=list, description="该窗口内匹配到的行号")
    content: str = Field(description="带行号的窗口内容")


class FindOutputSchema(BaseModel):
    # find_kb_document 的结构化输出
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="文件 ID")
    semantic: bool = Field(default=False, description="是否为语义查找")
    match_mode: Literal["keyword", "regex"] = Field(description="匹配模式")
    total_matches: int = Field(description="匹配到的行数")
    windows: list[FindWindowSchema] = Field(default_factory=list, description="上下文窗口")


class OpenInputSchema(BaseModel):
    # open_kb_document 的输入：line 与 offset 同时提供时 line 优先
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="要打开的文件 ID")
    line: int | None = Field(default=None, ge=1, description="可选，1-based 起始行号")
    offset: int | None = Field(default=None, ge=0, description="可选，0-based 起始偏移；line 优先于 offset")
    window_size: int = Field(default=1800, ge=1, le=2000, description="读取窗口行数")


class OpenOutputSchema(BaseModel):
    # open_kb_document 的结构化输出；next_offset 供长文连续读取
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="文件 ID")
    start_line: int = Field(description="窗口起始行号，1-based；空结果为 0")
    end_line: int = Field(description="窗口结束行号，1-based；空结果为 0")
    total_lines: int = Field(description="文件总行数")
    offset: int = Field(description="窗口起始偏移，0-based")
    window_size: int = Field(description="本次请求的窗口行数")
    has_more_before: bool = Field(description="窗口前是否还有内容")
    has_more_after: bool = Field(description="窗口后是否还有内容")
    next_offset: int | None = Field(default=None, description="下一窗口 offset；没有更多内容时为 null")
    content: str = Field(description="带行号的窗口内容")
