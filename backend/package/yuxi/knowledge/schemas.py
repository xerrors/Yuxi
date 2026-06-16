from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchInputSchema(BaseModel):
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    query_text: str = Field(description="检索关键词，应提炼为有助于召回答案的关键词或短语")
    file_name: str | None = Field(default=None, description="可选文件名关键词过滤，非必要不要使用")


class SearchResultSchema(BaseModel):
    id: str = Field(description="检索结果 ID，通常对应 chunk_id")
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(default="", description="结果所属文件 ID，可用于 Find/Open")
    content: str = Field(description="chunk 内容")
    metadata: dict[str, Any] = Field(default_factory=dict, description="来源、分数、chunk_index 等附加信息")


class SearchOutputSchema(BaseModel):
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    results: list[SearchResultSchema] = Field(default_factory=list, description="检索结果列表")


class FindInputSchema(BaseModel):
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="要检索的文件 ID")
    patterns: list[str] = Field(description="关键词或正则模式列表，至少提供一个")
    use_regex: bool = Field(default=False, description="是否将 patterns 作为正则表达式处理")
    case_sensitive: bool = Field(default=False, description="是否区分大小写")
    max_windows: int = Field(default=5, ge=1, le=20, description="最多返回的上下文窗口数量")
    window_size: int = Field(default=80, ge=1, le=200, description="每个上下文窗口的行数")


class FindWindowSchema(BaseModel):
    start_line: int = Field(description="窗口起始行号，1-based")
    end_line: int = Field(description="窗口结束行号，1-based")
    matched_lines: list[int] = Field(default_factory=list, description="该窗口内匹配到的行号")
    content: str = Field(description="带行号的窗口内容")


class FindOutputSchema(BaseModel):
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="文件 ID")
    semantic: bool = Field(default=False, description="是否为语义查找")
    match_mode: Literal["keyword", "regex"] = Field(description="匹配模式")
    total_matches: int = Field(description="匹配到的行数")
    windows: list[FindWindowSchema] = Field(default_factory=list, description="上下文窗口")


class OpenInputSchema(BaseModel):
    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    file_id: str = Field(description="要打开的文件 ID")
    line: int | None = Field(default=None, ge=1, description="可选，1-based 起始行号")
    offset: int | None = Field(default=None, ge=0, description="可选，0-based 起始偏移；line 优先于 offset")
    window_size: int = Field(default=1800, ge=1, le=2000, description="读取窗口行数")


class OpenOutputSchema(BaseModel):
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


class QueryKeywordsInputSchema(BaseModel):
    """基于关键词检索的输入模型"""

    kb_id: str = Field(description="知识库资源 ID，也就是 kb_id")
    keywords: list[str] = Field(
        description="关键词列表，用于 BM25 关键词检索；适合精确匹配专有名词、术语、代码符号等场景"
    )
    file_name: str | None = Field(default=None, description="可选文件名关键词过滤，非必要不要使用")
