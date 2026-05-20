from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

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


def count_tokens(text: str) -> int:
    """近似 token 计数，避免引入额外依赖。"""
    if not text:
        return 0
    # 英文单词 + 数字 + CJK 单字
    parts = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text)
    return max(1, len(parts)) if text.strip() else 0


def hard_split_by_token_limit(text: str, chunk_token_num: int) -> list[str]:
    """将文本按 token 上限硬切，用于 naive_merge 之后的兜底保护。"""
    token_iter = list(re.finditer(r"[A-Za-z0-9_]+|[一-鿿]", text or ""))
    if not token_iter:
        cleaned = (text or "").strip()
        return [cleaned] if cleaned else []

    chunks: list[str] = []
    start = 0
    index = 0
    max_tokens = max(int(chunk_token_num or 0), 1)

    while index < len(token_iter):
        next_index = min(index + max_tokens, len(token_iter))
        if next_index < len(token_iter):
            end = token_iter[next_index].start()
        else:
            end = len(text)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end
        index = next_index

    tail = text[start:].strip()
    if tail:
        chunks.append(tail)
    return chunks


def random_choices(arr: list[str], k: int) -> list[str]:
    if not arr:
        return []
    return random.choices(arr, k=min(len(arr), k))


def is_english(texts: str | list[str]) -> bool:
    if not texts:
        return False

    patt = re.compile(r"[`a-zA-Z0-9\s.,':;/\"?<>!\(\)\-]+")
    if isinstance(texts, str):
        seq = [texts]
    else:
        seq = [t for t in texts if isinstance(t, str) and t.strip()]

    if not seq:
        return False

    hits = sum(1 for t in seq if patt.fullmatch(t.strip()))
    return (hits / len(seq)) > 0.8


def not_bullet(line: str) -> bool:
    patt = [
        r"0",
        r"[0-9]+ +[0-9~个只-]",
        r"[0-9]+\.{2,}",
    ]
    return any(re.match(p, line) for p in patt)


def is_probable_heading_line(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False

    if re.match(r"^#{1,6}\s+\S", text):
        return True

    # 表格/HTML 残留通常不是标题。
    if re.search(r"</?(table|tr|td|th|caption|tbody|thead)[^>]*>", text, flags=re.IGNORECASE):
        return False

    # 超长行基本是正文或条款，不是章节标题。
    if len(text) > 96:
        return False
    if count_tokens(text) > 72:
        return False

    # 标题前段通常不会出现明显句号/逗号；出现则大概率是正文。
    if re.search(r"[，。；！？!?:：]", text[:24]):
        return False

    if text.endswith(("。", "；", "！", "!", "？", "?")) and len(text) > 20:
        return False

    return True


def _is_mid_sentence_bullet(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return False

    if re.match(r"^#{1,6}\s+\S", text):
        return False

    marker = re.search(
        r"([一二三四五六七八九十百]+、|[\(（][一二三四五六七八九十百]+[\)）]|[0-9]{1,2}[\.、])",
        text,
    )
    if not marker:
        return False

    if marker.start() == 0:
        return False

    prev = text[marker.start() - 1]
    return prev not in {"#", "\n"}


def bullets_category(sections: list[str]) -> int:
    hits: list[float] = [0.0] * len(BULLET_PATTERN)

    def bullet_weight(group_idx: int, line: str) -> float:
        # 对 markdown 标题候选增加权重，避免“正文里的 一、/（一）”压过真正的 # 标题层级。
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

    for i, pro in enumerate(BULLET_PATTERN):
        for sec in sections:
            sec = sec.strip()
            for p in pro:
                if re.match(p, sec) and not not_bullet(sec):
                    w = bullet_weight(i, sec)
                    if _is_mid_sentence_bullet(sec):
                        w *= 0.1
                    if i != MARKDOWN_BULLET_GROUP_INDEX and not is_probable_heading_line(sec):
                        w *= 0.2
                    hits[i] += w
                    break
    maximum = 0
    res = -1
    for i, hit in enumerate(hits):
        if hit <= maximum:
            continue
        res = i
        maximum = hit
    return res


def _get_text(section: str | tuple[str, str]) -> str:
    if isinstance(section, str):
        return section.strip()
    return (section[0] or "").strip()


def remove_contents_table(sections: list[str] | list[tuple[str, str]], eng: bool = False) -> None:
    i = 0
    while i < len(sections):
        line = re.sub(r"( |　|\u3000)+", "", _get_text(sections[i]).split("@@")[0], flags=re.IGNORECASE)
        if not re.match(r"(contents|目录|目次|tableofcontents|致谢|acknowledge)$", line, flags=re.IGNORECASE):
            i += 1
            continue

        sections.pop(i)
        if i >= len(sections):
            break

        prefix = _get_text(sections[i])[:3] if not eng else " ".join(_get_text(sections[i]).split()[:2])
        while not prefix and i < len(sections):
            sections.pop(i)
            if i >= len(sections):
                break
            prefix = _get_text(sections[i])[:3] if not eng else " ".join(_get_text(sections[i]).split()[:2])

        if i >= len(sections) or not prefix:
            break

        sections.pop(i)
        if i >= len(sections):
            break

        for j in range(i, min(i + 128, len(sections))):
            if not re.match(re.escape(prefix), _get_text(sections[j])):
                continue
            for _ in range(i, j):
                sections.pop(i)
            break


def make_colon_as_title(sections: list[str] | list[tuple[str, str]]) -> list[str] | list[tuple[str, str]]:
    if not sections:
        return sections
    if isinstance(sections[0], str):
        return sections

    i = 0
    while i < len(sections):
        text, layout = sections[i]
        i += 1
        text = text.split("@")[0].strip()
        if not text or text[-1] not in ":：":
            continue

        rev = text[::-1]
        arr = re.split(r"([。？！!?;；]| \.)", rev)
        if len(arr) < 2 or len(arr[1]) < 32:
            continue

        sections.insert(i - 1, (arr[0][::-1], "title"))
        i += 1

    return sections


def not_title(text: str) -> bool:
    if re.match(r"第[零一二三四五六七八九十百0-9]+条", text):
        return False
    if len(text.split()) > 12 or (" " not in text and len(text) >= 32):
        return True
    return bool(re.search(r"[,;，。；！!]", text))


def tree_merge(bull: int, sections: list[str] | list[tuple[str, str]], depth: int) -> list[str]:
    if not sections or bull < 0:
        return [s if isinstance(s, str) else s[0] for s in sections]

    if isinstance(sections[0], str):
        typed_sections: list[tuple[str, str]] = [(s, "") for s in sections]
    else:
        typed_sections = sections  # type: ignore[assignment]

    typed_sections = [
        (t, o)
        for t, o in typed_sections
        if t and len(t.split("@")[0].strip()) > 1 and not re.match(r"[0-9]+$", t.split("@")[0].strip())
    ]

    def get_level(section: tuple[str, str]) -> tuple[int, str]:
        text, layout = section
        text = re.sub(r"\u3000", " ", text).strip()

        for i, patt in enumerate(BULLET_PATTERN[bull]):
            if re.match(patt, text) and is_probable_heading_line(text):
                return i + 1, text

        if re.search(r"(title|head)", layout) and not not_title(text):
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

    sorted_levels = sorted(level_set)
    target_level = sorted_levels[depth - 1] if depth <= len(sorted_levels) else sorted_levels[-1]

    max_body_level = len(BULLET_PATTERN[bull]) + 2
    if target_level == max_body_level:
        target_level = sorted_levels[-2] if len(sorted_levels) > 1 else sorted_levels[0]

    root = Node(level=0, depth=target_level, texts=[])
    root.build_tree(lines)
    return [item for item in root.get_tree() if item]


def hierarchical_merge(bull: int, sections: list[str] | list[tuple[str, str]], depth: int) -> list[list[str]]:
    if not sections or bull < 0:
        return []

    if isinstance(sections[0], str):
        typed_sections: list[tuple[str, str]] = [(s, "") for s in sections]
    else:
        typed_sections = sections  # type: ignore[assignment]

    typed_sections = [
        (t, o)
        for t, o in typed_sections
        if t and len(t.split("@")[0].strip()) > 1 and not re.match(r"[0-9]+$", t.split("@")[0].strip())
    ]

    bullets_size = len(BULLET_PATTERN[bull])
    levels: list[list[int]] = [[] for _ in range(bullets_size + 2)]

    for i, (text, layout) in enumerate(typed_sections):
        for j, patt in enumerate(BULLET_PATTERN[bull]):
            if re.match(patt, text.strip()) and is_probable_heading_line(text):
                levels[j].append(i)
                break
        else:
            if re.search(r"(title|head)", layout) and not not_title(text):
                levels[bullets_size].append(i)
            else:
                levels[bullets_size + 1].append(i)

    pure_sections = [t for t, _ in typed_sections]

    def binary_search(arr: list[int], target: int) -> int:
        if not arr:
            return -1
        if target > arr[-1]:
            return len(arr) - 1
        if target < arr[0]:
            return -1

        s, e = 0, len(arr)
        while e - s > 1:
            mid = (e + s) // 2
            if target > arr[mid]:
                s = mid
            elif target < arr[mid]:
                e = mid
            else:
                return mid
        return s

    cks: list[list[int]] = []
    readed = [False] * len(pure_sections)
    levels = list(reversed(levels))
    for i, arr in enumerate(levels[:depth]):
        for j in arr:
            if readed[j]:
                continue
            readed[j] = True
            cks.append([j])
            if i + 1 == len(levels) - 1:
                continue

            for ii in range(i + 1, len(levels)):
                jj = binary_search(levels[ii], j)
                if jj < 0:
                    continue
                if levels[ii][jj] > cks[-1][-1]:
                    cks[-1].pop(-1)
                cks[-1].append(levels[ii][jj])

            for ii in cks[-1]:
                readed[ii] = True

    if not cks:
        return []

    for i in range(len(cks)):
        cks[i] = [pure_sections[j] for j in reversed(cks[i])]

    res: list[list[str]] = [[]]
    num = [0]
    for ck in cks:
        if len(ck) == 1:
            n = count_tokens(re.sub(r"@@[0-9]+.*", "", ck[0]))
            if n + num[-1] < 218:
                res[-1].append(ck[0])
                num[-1] += n
                continue
            res.append(ck)
            num.append(n)
            continue
        res.append(ck)
        num.append(218)

    return [chunk for chunk in res if chunk]


def _remove_pdf_tags(text: str) -> str:
    return re.sub(r"@@[0-9-]+\t[0-9.\t]+##", "", text or "")


def _extract_custom_delimiters(delimiter: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"`([^`]+)`", delimiter or "")]


def naive_merge(
    sections: str | list[str] | list[tuple[str, str]],
    chunk_token_num: int = 128,
    delimiter: str = "\n。；！？",
    overlapped_percent: int = 0,
) -> list[str]:
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

    custom_delimiters = _extract_custom_delimiters(delimiter)
    if custom_delimiters:
        pattern = "|".join(re.escape(t) for t in sorted(set(custom_delimiters), key=len, reverse=True))
        chunks: list[str] = []
        for sec, pos in typed_sections:
            split_sec = re.split(rf"({pattern})", sec, flags=re.DOTALL)
            for sub in split_sec:
                if re.fullmatch(pattern, sub or ""):
                    continue
                text = "\n" + sub
                local_pos = pos if count_tokens(text) >= 8 else ""
                if local_pos and local_pos not in text:
                    text += local_pos
                if text.strip():
                    chunks.append(text)
        return chunks

    if chunk_token_num <= 0:
        merged = "\n".join(sec for sec, _ in typed_sections if sec and sec.strip())
        return [merged] if merged.strip() else []

    chunks = [""]
    token_nums = [0]

    def add_chunk(text: str, pos: str) -> None:
        tnum = count_tokens(text)
        local_pos = pos or ""
        if tnum < 8:
            local_pos = ""

        threshold = chunk_token_num * (100 - overlap) / 100.0
        if chunks[-1] == "" or token_nums[-1] > threshold:
            if chunks:
                prev = _remove_pdf_tags(chunks[-1])
                start = int(len(prev) * (100 - overlap) / 100.0)
                text = prev[start:] + text
            if local_pos and local_pos not in text:
                text += local_pos
            chunks.append(text)
            token_nums.append(tnum)
        else:
            if local_pos and local_pos not in chunks[-1]:
                text += local_pos
            chunks[-1] += text
            token_nums[-1] += tnum

    for sec, pos in typed_sections:
        if not sec:
            continue
        add_chunk("\n" + sec, pos)

    return [chunk for chunk in chunks if chunk.strip()]


@dataclass
class Node:
    level: int
    depth: int = -1
    texts: list[str] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)

    def add_child(self, child_node: Node) -> None:
        self.children.append(child_node)

    def add_text(self, text: str) -> None:
        self.texts.append(text)

    def build_tree(self, lines: list[tuple[int, str]]) -> Node:
        stack: list[Node] = [self]
        for level, text in lines:
            if self.depth != -1 and level > self.depth:
                stack[-1].add_text(text)
                continue

            while len(stack) > 1 and level <= stack[-1].level:
                stack.pop()

            node = Node(level=level, texts=[text])
            stack[-1].add_child(node)
            stack.append(node)

        return self

    def get_tree(self) -> list[str]:
        tree_list: list[str] = []
        self._dfs(self, tree_list, [])
        return tree_list

    def _dfs(self, node: Node, tree_list: list[str], titles: list[str]) -> None:
        level = node.level
        texts = node.texts
        child = node.children

        if level == 0 and texts:
            tree_list.append("\n".join(titles + texts))

        path_titles = titles + texts if 1 <= level <= self.depth else titles

        if level > self.depth and texts:
            tree_list.append("\n".join(path_titles + texts))
        elif not child and (1 <= level <= self.depth):
            tree_list.append("\n".join(path_titles))

        for c in child:
            self._dfs(c, tree_list, path_titles)
