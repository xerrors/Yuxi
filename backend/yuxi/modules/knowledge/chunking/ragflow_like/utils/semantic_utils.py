"""语义切分工具：按 token 预算自动选簇数做嵌入聚类切分，并提供中英文混合分句。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sklearn.cluster import AgglomerativeClustering

# 通用缩写：句点后仍接小写字母续写（如 "etc. and"）时不是句界
_ENGLISH_ABBREVIATIONS = {
    "approx.",
    "dept.",
    "dr.",
    "e.g.",
    "etc.",
    "i.e.",
    "jr.",
    "mr.",
    "mrs.",
    "ms.",
    "no.",
    "prof.",
    "rev.",
    "sr.",
    "st.",
    "vs.",
}
# 称谓缩写：后接大写人名，句点一律不是句界
_ENGLISH_TITLE_ABBREVIATIONS = {"dr.", "jr.", "mr.", "mrs.", "ms.", "prof.", "rev.", "sr.", "st."}
# 大写缩略词（如 U.S.）后接这些小写词时视为新句开始而非续写
_ENGLISH_SENTENCE_STARTERS = {
    "he",
    "however",
    "i",
    "it",
    "meanwhile",
    "next",
    "she",
    "that",
    "then",
    "these",
    "this",
    "those",
    "they",
    "we",
    "you",
}


def semantic_chunking_with_auto_clusters(
    text: str,
    embed_fn: Callable[[list[str]], Any] | None,
    token_count_fn: Callable[[str], int],
    max_chunk_size: int = 512,
) -> list[str]:
    """
    语义切分文本，簇数按 token 预算自动确定。

    逻辑：
    - 先用 split_mixed_sentences 按语言分句；不足两句时整段文本作为一个 chunk 返回。
    - 对每个句子计数 token；embed_fn 缺失或全文未超预算时不聚类，按预算顺序合并句子。
    - 否则对句子嵌入，簇数取「总 token / max_chunk_size」向上取整（不超过句数），
      用余弦相似度平均联动聚类；按原文顺序在簇标签变化或超预算处切出连续分块。
    """
    sentences = split_mixed_sentences(text)
    if len(sentences) < 2:
        return [text.strip()]

    sentence_token_counts = [token_count_fn(sentence) for sentence in sentences]
    total_tokens = sum(sentence_token_counts)

    # 无嵌入函数或未超预算：退化为按 token 预算顺序合并，不做聚类
    if embed_fn is None or total_tokens <= max_chunk_size:
        chunks = []
        current_chunk = ""
        current_chunk_tokens = 0
        for sentence, token_count in zip(sentences, sentence_token_counts, strict=True):
            # 首句置空时 current_chunk 为空串，避免产出空 chunk
            if current_chunk and current_chunk_tokens + token_count > max_chunk_size:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
                current_chunk_tokens = token_count
            else:
                current_chunk += sentence
                current_chunk_tokens += token_count
        if current_chunk:
            chunks.append(current_chunk.strip())
        return chunks

    embeddings = embed_fn(sentences)
    # 聚类标签的条数等于向量行数；不足或多余都会让下面的 zip 截断，静默丢弃句子。
    if len(embeddings) != len(sentences):
        raise ValueError(
            f"semantic chunking expects one embedding per sentence, got {len(embeddings)} embeddings for {len(sentences)} sentences"
        )

    # 簇数按预算向上取整，避免整除时多切一块；聚类数不能超过句子数
    best_k = min((total_tokens + max_chunk_size - 1) // max_chunk_size, len(sentences))

    # labels[i] 是第 i 句的簇标签（如 [0,0,1,2,2]），后续按原文顺序在标签变化处切出连续分块
    labels = AgglomerativeClustering(n_clusters=best_k, metric="cosine", linkage="average").fit_predict(embeddings)

    chunks = []
    current_chunk = ""
    current_chunk_tokens = 0
    current_label = labels[0]

    for sentence, label, token_count in zip(sentences, labels, sentence_token_counts, strict=True):
        if label != current_label or current_chunk_tokens + token_count > max_chunk_size:
            # 空块只会出现在首句超预算时，跳过避免产出空 chunk
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = sentence
            current_chunk_tokens = token_count
            current_label = label
        else:
            current_chunk += sentence
            current_chunk_tokens += token_count

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def split_mixed_sentences(text: str) -> list[str]:
    """
    中英文混合分句：按换行切物理段落，再按段落语言分发分句策略。

    含英文字母的段落按英文句末标点分句（保留常见缩写与引号边界）；
    其余段落交给 split_sentences_chinese，无结果时按中文句末标点强制分割兜底。
    返回去除两端空白且非空的句子列表，换行只作段落分隔、不保留在句子中。
    """
    paragraphs = re.split(r"\n+", text)
    sentences = []

    for paragraph in paragraphs:
        if not paragraph.strip():
            continue
        if re.search(r"[A-Za-z]", paragraph):
            parts = _split_english_sentences(paragraph)
        else:
            parts = split_sentences_chinese(paragraph) or re.split(r"(?<=[。！？])", paragraph)
        sentences.extend(part.strip() for part in parts if part.strip())

    return sentences


def split_sentences_chinese(text: str) -> list[str]:
    """
    使用正则表达式将中文文本分割成句子。

    逻辑：
    - 匹配中文句号、感叹号、问号（。！？）作为分隔点。
    - 使用正向/反向预查处理引号：确保如果标点后面紧跟引号（”’"），该引号会被保留在当前句子末尾，而不是被切分到下一句。
    - 返回去除两端空格且非空的句子列表。
    """
    pattern = r'(?<=[。！？][”’"])|(?<=[。！？])(?![”’"])'
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


# ---------- 英文分句实现 ----------


def _split_english_sentences(text: str) -> list[str]:
    """按英文句末标点分句：缩写句点不切句，句末引号与括号保留在句子结尾。"""
    sentence_end_marks = ".!?。！？"
    closing_marks = "\"'”’)]}"
    sentences: list[str] = []
    start = 0
    index = 0

    while index < len(text):
        if text[index] not in sentence_end_marks:
            index += 1
            continue

        # 吞掉连续句末标点（如 "!!!"、"..."），再吞掉收尾引号/括号使其留在句末
        punctuation_end = index + 1
        while punctuation_end < len(text) and text[punctuation_end] in sentence_end_marks:
            punctuation_end += 1
        boundary_end = punctuation_end
        while boundary_end < len(text) and text[boundary_end] in closing_marks:
            boundary_end += 1

        # 句界成立条件：标点串之后是文本末尾或空白；缩写句点（如 p.m.、Dr.）推翻句界
        is_boundary = boundary_end == len(text) or text[boundary_end].isspace()
        if text[index] == "." and _is_english_abbreviation(text, start, index):
            is_boundary = False
        if is_boundary:
            sentence = text[start:boundary_end].strip()
            if sentence:
                sentences.append(sentence)
            start = boundary_end
        index = boundary_end

    remainder = text[start:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def _is_english_abbreviation(text: str, start: int, punctuation: int) -> bool:
    """判断句点是否属于常见英文缩写、人名首字母或字母缩略词，而不是句界。"""
    # 句点前缀必须是“以字母开头的单词加句点”（排除小数点等）
    prefix = text[start : punctuation + 1].rstrip()
    match = re.search(r"([A-Za-z](?:[A-Za-z.]*)\.)$", prefix)
    if match is None:
        return False

    token = match.group(1).lower()
    letters = token.replace(".", "")
    if token in _ENGLISH_TITLE_ABBREVIATIONS:
        return True

    # 句点串之后第一个非空白字符，供后续分支区分续写与新句
    next_index = punctuation + 1
    while next_index < len(text) and text[next_index].isspace():
        next_index += 1
    next_character = text[next_index] if next_index < len(text) else ""

    if token in _ENGLISH_ABBREVIATIONS:
        # 通用缩写：后接小写（"etc. and"）视为续写，否则是新句
        return bool(next_character) and next_character.islower()
    if len(letters) == 1:
        # 人名首字母缩写（J. R. R.）
        return True
    if re.fullmatch(r"(?:[A-Z]\.){2,}", match.group(1)):
        # 大写缩略词（U.S.）：后接小写句首词（he、next 等）说明新句开始
        next_word = re.match(r"[A-Za-z]+", text[next_index:])
        return next_word is not None and next_word.group(0).lower() not in _ENGLISH_SENTENCE_STARTERS
    if re.fullmatch(r"(?:[a-z]\.){2,}", token):
        # 小写缩略词（a.m.、p.m.）：文本结束或后接小写时视为续写
        return next_index == len(text) or text[next_index].islower()
    return False
