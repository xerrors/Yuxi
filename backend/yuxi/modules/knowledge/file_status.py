"""文件状态常量契约，以及筛选读取时的历史值展开映射。"""


class FileStatus:
    """文件状态常量；取值是数据库存量、前端与 repositories 共用的持久化契约，只增不改。

    保持纯字符串常量而非 Enum，调用方直接把它当普通字符串用于 SQL 过滤、字典键与响应值。
    """

    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    ERROR_PARSING = "error_parsing"
    INDEXING = "indexing"
    INDEXED = "indexed"
    ERROR_INDEXING = "error_indexing"
    DELETED = "deleted"
    # 文件夹与文件共用 status 列，目录创建即就绪，固定写 done。
    FOLDER_READY = "done"


# parse_file 可以领取的状态。
PARSE_CLAIM_STATUSES = frozenset({FileStatus.UPLOADED, FileStatus.ERROR_PARSING})

# index_file 可以领取的状态。done/failed 是历史文件值，done 与文件夹就绪同字面量。
INDEX_CLAIM_STATUSES = frozenset(
    {
        FileStatus.PARSED,
        FileStatus.ERROR_INDEXING,
        FileStatus.INDEXED,
        FileStatus.FOLDER_READY,
        "failed",
    }
)


def needs_parse_before_index(file_meta: dict) -> bool:
    """入库请求在执行器领取前是否要先解析。

    未解析和解析失败直接解析。可入库但没有正文时也先解析，执行器不再把这种文件领走再退回。
    """

    if file_meta.get("is_folder"):
        return False
    status = file_meta.get("status")
    if status in PARSE_CLAIM_STATUSES:
        return True
    return status in INDEX_CLAIM_STATUSES and not file_meta.get("markdown_file")


def status_filter_values(status: str | None) -> set[str] | None:
    """把筛选状态展开为可接受的持久化值集合，列表与跨库搜索共用。

    空值与前端约定的 all 返回 None 表示不过滤；历史值只在读取时并入，不改写已有记录。
    """
    if not status or status == "all":
        return None
    # done/failed 是旧版本写入的存量值；done 与 FOLDER_READY 同值不同义，保留字面量。
    return {
        FileStatus.INDEXED: {FileStatus.INDEXED, "done"},
        FileStatus.ERROR_INDEXING: {FileStatus.ERROR_INDEXING, "failed"},
    }.get(status, {status})
