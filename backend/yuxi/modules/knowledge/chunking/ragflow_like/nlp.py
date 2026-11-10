"""RAGFlow 思路移植的文本处理核心：token 计数、编号层级识别与三种合并策略。

general/book/laws 等分块器共享本模块，公开函数的签名与返回值是对外契约。
文本拼接一律经 join_source_text 保留字符来源，供入库时定位原文行号。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from yuxi.modules.knowledge.chunking.source_spans import join_source_text

# 编号体系分组：组内模式按层级从粗到细排列，由 bullets_category 打分选出全文体系。
# 第 MARKDOWN_BULLET_GROUP_INDEX 组是 markdown 标题层级，laws 分块器直接引用该下标。
BULLET_PATTERN = [
    [
        r"第[零一二三四五六七八九十百0-9]+(分?编|部分)",
        r"第[零一二三四五六七八九十百0-9]+章",
        r"第[零一二三四五六七八九十百0-9]+节",
        r"第[零一二三四五六七八九十百0-9]+条",
        r"[\(（][零一二三四五六七八九十百]+[\)）]",
    ],
    [
        r"第[0-9]+章",
        r"第[0-9]+节",
        r"[0-9]{,2}[\. 、]",
        r"[0-9]{,2}\.[0-9]{,2}[^a-zA-Z/%~-]",
        r"[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}",
        r"[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}",
    ],
    [
        r"第[零一二三四五六七八九十百0-9]+章",
        r"第[零一二三四五六七八九十百0-9]+节",
        r"[零一二三四五六七八九十百]+[ 、]",
        r"[\(（][零一二三四五六七八九十百]+[\)）]",
        r"[\(（][0-9]{,2}[\)）]",
    ],
    [
        r"PART (ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)",
        r"Chapter (I+V?|VI*|XI|IX|X)",
        r"Section [0-9]+",
        r"Article [0-9]+",
    ],
    [
        r"^#[^#]",
        r"^##[^#]",
        r"^###.*",
        r"^####.*",
        r"^#####.*",
        r"^######.*",
    ],
]

MARKDOWN_BULLET_GROUP_INDEX = 4

# token 近似切分：英文单词/数字串整体算一个 token，CJK 逐字计数；计数与硬切共用。
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


# ── token 计数 ──────────────────────────────────────────────────────


def count_tokens(text: str) -> int:
    """近似 token 计数，避免引入额外依赖。"""
    if not text or not text.strip():
        return 0
    return max(1, len(_TOKEN_PATTERN.findall(text)))


# ── 合并流水线（分块器主路径）───────────────────────────────────────


def naive_merge(
    sections: str | list[str] | list[tuple[str, str]],
    chunk_token_num: int = 128,
    delimiter: str | None = None,
    overlapped_percent: int = 0,
) -> list[str]:
    """按 token 预算贪心合并相邻段落；general 的主路径，也是其余分块器的兜底。

    sections 允许整篇文本、纯段落列表或 (文本, 布局) 列表；位置标记 pos 仅在
    片段达 8 token 时随块附带（RAGFlow 遗留的 PDF 定位协议）。

    delimiter 是片段之间的连接符，不是切分符——切分已由调用方完成。传 None 表示
    片段边界来自文档结构（book/laws 按行取段），此时保留换行，避免把原文的行间
    换行改写成分隔符配置。
    """
    if not sections:
        return []

    if isinstance(sections, str):
        typed_sections: list[tuple[str, str]] = [(sections, "")]
    elif isinstance(sections[0], str):
        typed_sections = [(s, "") for s in sections]  # type: ignore[index]
    else:
        typed_sections = sections  # type: ignore[assignment]

    chunk_token_num = max(int(chunk_token_num or 0), 0)
    overlap = max(0, min(int(overlapped_percent or 0), 99))

    custom_delimiters = _extract_custom_delimiters(delimiter or "")
    if custom_delimiters:
        return _split_by_custom_delimiters(typed_sections, custom_delimiters)

    # 连接符默认换行：未传 delimiter 时片段边界来自文档结构，行间换行必须保留。
    separator = delimiter or "\n"

    if chunk_token_num <= 0:
        merged = join_source_text((sec for sec, _ in typed_sections if sec and sec.strip()), separator)
        return [merged] if merged.strip() else []

    # 当前块 token 超过该阈值就开启新块；overlap 越大，上一块可继续追加的预算越小。
    threshold = chunk_token_num * (100 - overlap) / 100.0
    chunks: list[str] = [""]  # 首个空串是哨兵，返回前统一过滤
    token_nums = [0]

    def add_chunk(sec: str, pos: str, sep: str) -> None:
        """把片段并入当前块；预算耗尽时按 overlap 截取上一块尾部作为新块前缀。

        连接符只在并入已有块时加；片段自己开启新块时不带前导分隔符。overlap 留下
        的尾部非空时它仍与当前块相邻，需要连接符隔开，否则尾部会和片段粘成一个词。
        """
        tnum = count_tokens(sec)
        local_pos = pos if tnum >= 8 and pos else ""
        if chunks[-1] and token_nums[-1] <= threshold:
            text = chunks[-1] + sep + sec if sep else chunks[-1] + sec
            if local_pos and local_pos not in text:
                text += local_pos
            chunks[-1] = text
            token_nums[-1] += tnum + count_tokens(sep)
            return

        prev = _remove_pdf_tags(chunks[-1])
        start = int(len(prev) * (100 - overlap) / 100.0)
        tail = prev[start:]
        text = tail + sep + sec if tail and sep else tail + sec
        if local_pos and local_pos not in text:
            text += local_pos
        chunks.append(text)
        token_nums.append(tnum)

    is_first = True
    for sec, pos in typed_sections:
        if not sec:
            continue
        add_chunk(sec, pos, "" if is_first else separator)
        is_first = False

    return [chunk for chunk in chunks if chunk.strip()]


@dataclass
class Node:
    """标题层级树节点：build_tree 按 (层级, 文本) 建树，get_tree 输出带标题路径的块。"""

    level: int
    depth: int = -1
    texts: list[str] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)

    def build_tree(self, lines: list[tuple[int, str]]) -> Node:
        """按层级下降挂节点；比 depth 更深的行作为正文挂在当前最深标题下。"""
        stack: list[Node] = [self]
        for level, text in lines:
            if self.depth != -1 and level > self.depth:
                stack[-1].texts.append(text)
                continue

            # 回退到最近的严格更粗祖先，再把新节点挂为它的子节点。
            while len(stack) > 1 and level <= stack[-1].level:
                stack.pop()

            node = Node(level=level, texts=[text])
            stack[-1].children.append(node)
            stack.append(node)

        return self

    def get_tree(self) -> list[str]:
        """深度优先输出块列表，每块自带其祖先标题路径。"""
        tree_list: list[str] = []
        self._dfs(self, tree_list, [])
        return tree_list

    def _dfs(self, node: Node, tree_list: list[str], titles: list[str]) -> None:
        level = node.level
        texts = node.texts
        children = node.children

        # 根节点自带正文时先独立成块（层级 0 只出现在根）。
        if level == 0 and texts:
            tree_list.append(join_source_text(titles + texts, "\n"))

        # 标题层（1..depth）的文本进入后续路径；正文层不进入。
        path_titles = titles + texts if 1 <= level <= self.depth else titles

        if level > self.depth and texts:
            tree_list.append(join_source_text(path_titles + texts, "\n"))
        elif not children and 1 <= level <= self.depth:
            # 无正文也无子节点的标题叶节点也输出，避免丢标题。
            tree_list.append(join_source_text(path_titles, "\n"))

        for child in children:
            self._dfs(child, tree_list, path_titles)


def tree_merge(bull: int, sections: list[str] | list[tuple[str, str]], depth: int) -> list[str]:
    """按标题层级树合并段落（laws 主路径）：以目标层级为块边界，子层内容并入。"""
    if not sections or bull < 0:
        return [s if isinstance(s, str) else s[0] for s in sections]

    typed_sections = _normalize_sections(sections)

    def get_level(section: tuple[str, str]) -> tuple[int, str]:
        """编号命中的模式序号 +1 为标题层，布局 title/head 为次深层，其余为正文层。"""
        text, layout = section
        # 全角空格归一为半角空格后再匹配层级模式（RAGFlow 做法），并保留字符来源。
        text = join_source_text(text.split("\u3000"), " ").strip()

        for i, patt in enumerate(BULLET_PATTERN[bull]):
            if re.match(patt, text) and _is_probable_heading_line(text):
                return i + 1, text

        if re.search(r"(title|head)", layout) and _is_title_like(text):
            return len(BULLET_PATTERN[bull]) + 1, text

        return len(BULLET_PATTERN[bull]) + 2, text

    lines: list[tuple[int, str]] = []
    level_set: set[int] = set()
    for section in typed_sections:
        level, text = get_level(section)
        if not text.strip("\n"):
            continue
        lines.append((level, text))
        level_set.add(level)

    if not lines:
        return []

    # depth 指定以出现过的层级中第 depth 个（从粗到细）作为块边界；
    # 正文层不能当边界，退而取次细的标题层。
    sorted_levels = sorted(level_set)
    target_level = sorted_levels[depth - 1] if depth <= len(sorted_levels) else sorted_levels[-1]
    body_level = len(BULLET_PATTERN[bull]) + 2
    if target_level == body_level:
        target_level = sorted_levels[-2] if len(sorted_levels) > 1 else sorted_levels[0]

    root = Node(level=0, depth=target_level, texts=[])
    root.build_tree(lines)
    return [item for item in root.get_tree() if item]


def hierarchical_merge(bull: int, sections: list[str] | list[tuple[str, str]], depth: int) -> list[list[str]]:
    """按标题层级聚合段落（book 主路径），返回每个 chunk 的段落列表。"""
    if not sections or bull < 0:
        return []

    typed_sections = _normalize_sections(sections)

    bullets_size = len(BULLET_PATTERN[bull])
    # levels[k] 收集第 k 层的段落下标：0..bullets_size-1 为编号层级（粗到细），
    # bullets_size 为布局 title/head，bullets_size+1 为普通正文。
    levels: list[list[int]] = [[] for _ in range(bullets_size + 2)]
    for i, (text, layout) in enumerate(typed_sections):
        for j, patt in enumerate(BULLET_PATTERN[bull]):
            if re.match(patt, text.strip()) and _is_probable_heading_line(text):
                levels[j].append(i)
                break
        else:
            if re.search(r"(title|head)", layout) and _is_title_like(text):
                levels[bullets_size].append(i)
            else:
                levels[bullets_size + 1].append(i)

    pure_sections = [t for t, _ in typed_sections]

    def binary_search(arr: list[int], target: int) -> int:
        """返回 arr 中不超过 target 的最大元素下标；target 小于全部元素时为 -1。"""
        if not arr:
            return -1
        if target > arr[-1]:
            return len(arr) - 1
        if target < arr[0]:
            return -1

        low, high = 0, len(arr)
        while high - low > 1:
            mid = (high + low) // 2
            if target > arr[mid]:
                low = mid
            elif target < arr[mid]:
                high = mid
            else:
                return mid
        return low

    # 层级序列反转后按“正文 → title → 最细编号 → … → 最粗编号”展开，取前 depth 层：
    # 每个标题先自成一组，再向上把之前最近的粗层标题并入同组。
    index_groups: list[list[int]] = []
    consumed = [False] * len(pure_sections)
    levels = list(reversed(levels))
    for i, arr in enumerate(levels[:depth]):
        for j in arr:
            if consumed[j]:
                continue
            consumed[j] = True
            index_groups.append([j])
            # 倒数第二层之后不再向上挂靠（沿用 RAGFlow 原始边界语义）。
            if i + 1 == len(levels) - 1:
                continue

            for upper in range(i + 1, len(levels)):
                jj = binary_search(levels[upper], j)
                if jj < 0:
                    continue
                if levels[upper][jj] > index_groups[-1][-1]:
                    index_groups[-1].pop(-1)
                index_groups[-1].append(levels[upper][jj])

            for member in index_groups[-1]:
                consumed[member] = True

    if not index_groups:
        return []

    # 组内下标反转为“粗标题在前、细内容在后”的文本序列。
    cks: list[list[str]] = []
    for group in index_groups:
        cks.append([pure_sections[j] for j in reversed(group)])

    # 单标题短块并入当前 chunk；多元素块视为已饱和（token 记为阈值本身），后续短块不再并入。
    # 阈值 218 为 RAGFlow 经验值。
    merge_below_tokens = 218
    res: list[list[str]] = [[]]
    num = [0]
    for ck in cks:
        if len(ck) == 1:
            n = count_tokens(re.sub(r"@@[0-9]+.*", "", ck[0]))
            if n + num[-1] < merge_below_tokens:
                res[-1].append(ck[0])
                num[-1] += n
                continue
            res.append(ck)
            num.append(n)
            continue
        res.append(ck)
        num.append(merge_below_tokens)

    return [chunk for chunk in res if chunk]


# ── token 上限保护 ──────────────────────────────────────────────────


def hard_split_by_token_limit(text: str, chunk_token_num: int, hard_limit_token_num: int | None = None) -> list[str]:
    """将文本按 token 上限硬切，用于 naive_merge 之后的兜底保护。

    hard_limit_token_num 只在调用方显式传入时生效：略超目标的尾块允许并入前一块
    保持完整；默认严格不超过 chunk_token_num。
    """
    tokens = list(_TOKEN_PATTERN.finditer(text or ""))
    if not tokens:
        cleaned = (text or "").strip()
        return [cleaned] if cleaned else []

    max_tokens = max(int(chunk_token_num or 0), 1)
    hard_limit = None
    if hard_limit_token_num is not None:
        hard_limit = max(int(hard_limit_token_num or 0), max_tokens)
        if len(tokens) <= hard_limit:
            cleaned = (text or "").strip()
            return [cleaned] if cleaned else []

    # 以每 max_tokens 个 token 为窗口取原文 span，空白窗口直接丢弃。
    spans: list[tuple[int, int]] = []
    start = 0
    cursor = 0
    while cursor < len(tokens):
        next_cursor = min(cursor + max_tokens, len(tokens))
        end = tokens[next_cursor].start() if next_cursor < len(tokens) else len(text)
        if text[start:end].strip():
            spans.append((start, end))
        start = end
        cursor = next_cursor

    # 窗口循环结束后残余的非空白尾巴单独成块。
    if text[start:].strip():
        spans.append((start, len(text)))

    if hard_limit is not None and len(spans) >= 2:
        # 尾块并入前一块后仍不超 hard_limit 时合并，避免出现碎片小块。
        prev_start, _ = spans[-2]
        _, tail_end = spans[-1]
        candidate = text[prev_start:tail_end].strip()
        if count_tokens(candidate) <= hard_limit:
            spans[-2] = (prev_start, tail_end)
            spans.pop()

    return [text[start:end].strip() for start, end in spans if text[start:end].strip()]


# ── 层级识别与文档预处理 ────────────────────────────────────────────


def bullets_category(sections: list[str]) -> int:
    """识别全文的编号体系，返回 BULLET_PATTERN 组下标；无可信命中时返回 -1。"""
    hits: list[float] = [0.0] * len(BULLET_PATTERN)

    def bullet_weight(group_idx: int, line: str) -> float:
        # markdown 标题按层级加权，避免“正文里的 一、/（一）”压过真正的 # 标题层级。
        if group_idx != MARKDOWN_BULLET_GROUP_INDEX:
            return 1.0

        heading = line.strip()
        if not re.match(r"^#{1,6}\s+\S", heading):
            return 1.0

        level = len(heading) - len(heading.lstrip("#"))
        if level <= 2:
            return 4.0
        if level <= 4:
            return 3.0
        return 2.0

    for group_idx, patterns in enumerate(BULLET_PATTERN):
        for raw in sections:
            sec = raw.strip()
            for patt in patterns:
                if re.match(patt, sec) and not _is_pseudo_numbering(sec):
                    weight = bullet_weight(group_idx, sec)
                    if _is_mid_sentence_bullet(sec):
                        # 句中枚举只保留一成权重。
                        weight *= 0.1
                    if group_idx != MARKDOWN_BULLET_GROUP_INDEX and not _is_probable_heading_line(sec):
                        # 非 markdown 组里不像标题的命中降权，避免目录点线等噪声夺冠。
                        weight *= 0.2
                    hits[group_idx] += weight
                    break

    best_index, best_hits = -1, 0.0
    for group_idx, hit in enumerate(hits):
        if hit > best_hits:
            best_index, best_hits = group_idx, hit
    return best_index


def random_choices(arr: list[str], k: int) -> list[str]:
    """无放回抽样，短文档保留全部段落供语言与标题识别。"""
    if not arr:
        return []
    return random.sample(arr, k=min(len(arr), k))


def is_english(texts: str | list[str]) -> bool:
    """判断样本是否以英文为主：八成以上整行仅由英文常见字符构成即视为英文。"""
    if not texts:
        return False

    if isinstance(texts, str):
        samples = [texts]
    else:
        samples = [t for t in texts if isinstance(t, str) and t.strip()]

    if not samples:
        return False

    pattern = re.compile(r"[`a-zA-Z0-9\s.,':;/\"?<>!\(\)\-]+")
    hits = sum(1 for t in samples if pattern.fullmatch(t.strip()))
    return hits / len(samples) > 0.8


def remove_contents_table(sections: list[str] | list[tuple[str, str]], eng: bool = False) -> None:
    """原地删除目录块：以“目录/Contents/致谢”标题行为界，清掉目录条目。"""

    def entry_prefix(index: int) -> str:
        # 目录首条目的识别前缀：中文取前 3 字，英文取前两个单词。
        text = _get_text(sections[index])
        return text[:3] if not eng else " ".join(text.split()[:2])

    i = 0
    while i < len(sections):
        # 去掉空格后整行恰为目录标题词，才认作目录块起点。
        line = re.sub(r"[ \u3000]+", "", _get_text(sections[i]).split("@@")[0])
        if not re.match(r"(contents|目录|目次|tableofcontents|致谢|acknowledge)$", line, flags=re.IGNORECASE):
            i += 1
            continue

        sections.pop(i)
        if i >= len(sections):
            break

        prefix = entry_prefix(i)
        while not prefix and i < len(sections):
            # 跳过空段落，直到取到第一个非空目录条目。
            sections.pop(i)
            if i >= len(sections):
                break
            prefix = entry_prefix(i)

        if i >= len(sections) or not prefix:
            break

        # 首条目本身也属于目录，先删除。
        sections.pop(i)
        if i >= len(sections):
            break

        # 向后找首个仍以该前缀开头的段落（通常是正文里真正的同名章节标题），
        # 其前的段落视为目录条目删除；最多向前看 128 段。
        for j in range(i, min(i + 128, len(sections))):
            if not _get_text(sections[j]).startswith(prefix):
                continue
            for _ in range(i, j):
                sections.pop(i)
            break


# ── 内部辅助：标题与编号启发式 ──────────────────────────────────────


def _is_probable_heading_line(line: str) -> bool:
    """启发式判断单行是否像章节标题，供层级分级与打分降噪使用。"""
    text = (line or "").strip()
    if not text:
        return False

    if re.match(r"^#{1,6}\s+\S", text):
        return True

    # 表格/HTML 残留通常不是标题。
    if re.search(r"</?(table|tr|td|th|caption|tbody|thead)[^>]*>", text, flags=re.IGNORECASE):
        return False

    # 超长行基本是正文或条款，不是章节标题。
    if len(text) > 96 or count_tokens(text) > 72:
        return False

    # 标题前段通常不出现句读；出现则大概率是正文。
    if re.search(r"[，。；！？!?:：]", text[:24]):
        return False

    if text.endswith(("。", "；", "！", "!", "？", "?")) and len(text) > 20:
        return False

    return True


def _is_mid_sentence_bullet(line: str) -> bool:
    """判断编号标记是否出现在行中间而非行首：句中枚举不是标题层级。"""
    text = (line or "").strip()
    if not text or re.match(r"^#{1,6}\s+\S", text):
        return False

    marker = re.search(
        r"([一二三四五六七八九十百]+、|[\(（][一二三四五六七八九十百]+[\)）]|[0-9]{1,2}[\.、])",
        text,
    )
    if not marker or marker.start() == 0:
        return False

    # 标记前是 # 或换行说明仍在行首，其余视为句中枚举。
    return text[marker.start() - 1] not in {"#", "\n"}


def _is_pseudo_numbering(line: str) -> bool:
    """识别伪装成层级编号的行首数字：孤立的 0、数量表达与目录点线页码。"""
    # 整行恰为 0 才是伪编号；0. 与 0.1 仍是十进制标题。
    patterns = (r"0$", r"[0-9]+ +[0-9~个只-]", r"[0-9]+\.{2,}")
    return any(re.match(p, line) for p in patterns)


def _is_title_like(text: str) -> bool:
    """判断文本是否像标题：法条编号视作标题；词数过多、过长或含句读的是正文。"""
    if re.match(r"第[零一二三四五六七八九十百0-9]+条", text):
        return True
    if len(text.split()) > 12 or (" " not in text and len(text) >= 32):
        return False
    return not re.search(r"[,;，。；！!]", text)


# ── 内部辅助：段落归一化与片段处理 ──────────────────────────────────


def _normalize_sections(sections: list[str] | list[tuple[str, str]]) -> list[tuple[str, str]]:
    """补全 (文本, 布局) 形态并过滤不成段的碎片；调用方需保证 sections 非空。"""
    if isinstance(sections[0], str):
        typed: list[tuple[str, str]] = [(s, "") for s in sections]  # type: ignore[index]
    else:
        typed = list(sections)  # type: ignore[assignment]

    normalized: list[tuple[str, str]] = []
    for text, layout in typed:
        # PDF 位置标签是 @@数字<TAB>坐标##。按单个 @ 截断会丢掉邮箱、提及和年份标记。
        head = _remove_pdf_tags(text).strip()
        # 丢弃空白、单字符与纯数字行：既不是标题也不是可合并正文。
        if len(head) > 1 and not re.match(r"[0-9]+$", head):
            normalized.append((text, layout))
    return normalized


def _get_text(section: str | tuple[str, str]) -> str:
    """统一取段落实文：兼容纯字符串与 (文本, 布局) 元组两种形态。"""
    if isinstance(section, str):
        return section.strip()
    return (section[0] or "").strip()


def _split_by_custom_delimiters(typed_sections: list[tuple[str, str]], custom_delimiters: list[str]) -> list[str]:
    """按反引号声明的自定义分隔符切段：分隔符独立成片后丢弃，其余片段直接输出。"""
    # 长分隔符优先匹配，避免短分隔符截断长分隔符。
    pattern = "|".join(re.escape(t) for t in sorted(set(custom_delimiters), key=len, reverse=True))
    chunks: list[str] = []
    for sec, pos in typed_sections:
        boundaries = [(match.start(), match.end()) for match in re.finditer(pattern, sec, flags=re.DOTALL)]
        starts = [0, *(end for _, end in boundaries)]
        ends = [*(start for start, _ in boundaries), len(sec)]
        split_sec = [sec[start:end] for start, end in zip(starts, ends, strict=True)]
        for sub in split_sec:
            if re.fullmatch(pattern, sub):
                continue
            text = "\n" + sub
            local_pos = pos if count_tokens(text) >= 8 else ""
            if local_pos and local_pos not in text:
                text += local_pos
            if text.strip():
                chunks.append(text)
    return chunks


def _remove_pdf_tags(text: str) -> str:
    """移除历史 PDF 布局标签，保留其余字符的来源。"""
    parts = []
    start = 0
    for match in re.finditer(r"@@[0-9-]+\t[0-9.\t]+##", text or ""):
        parts.append(text[start : match.start()])
        start = match.end()
    parts.append(text[start:])
    return join_source_text(parts)


def _extract_custom_delimiters(delimiter: str) -> list[str]:
    """提取 delimiter 配置中反引号包裹的自定义分隔符。"""
    return [m.group(1) for m in re.finditer(r"`([^`]+)`", delimiter or "")]


def custom_delimiter_config(delimiter: str) -> str | None:
    """只保留反引号声明的自定义切分部分，供按行取段的分块器透传给 naive_merge。

    book/laws 的片段边界来自文档结构，配置里的普通值对它们是切分配置而不是连接符；
    直接透传原值会把行间换行改写成该分隔符。返回 None 表示没有自定义切分配置，
    由 naive_merge 回退到换行连接。
    """
    custom_delimiters = _extract_custom_delimiters(delimiter)
    if not custom_delimiters:
        return None
    return "".join(f"`{item}`" for item in custom_delimiters)
