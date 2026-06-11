from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


@dataclass(frozen=True)
class StandardSpec:
    standard_no: str
    official_title: str
    source_url: str


STANDARDS = [
    StandardSpec(
        standard_no="GB/T 45001-2020",
        official_title="职业健康安全管理体系 要求及使用指南",
        source_url="https://std.samr.gov.cn/gb/search/gbDetailed?id=A02801294949EBB4E05397BE0A0AB6FE",
    ),
    StandardSpec(
        standard_no="GB/T 24001-2016",
        official_title="环境管理体系 要求及使用指南",
        source_url="https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D81112D3A7E05397BE0A0AB82A",
    ),
    StandardSpec(
        standard_no="GB/T 19001-2016",
        official_title="质量管理体系 要求",
        source_url="https://std.samr.gov.cn/gb/search/gbDetailed?id=71F772D814AED3A7E05397BE0A0AB82A",
    ),
]


def normalize_standard_no(value: str) -> str | None:
    match = re.search(r"\bGB\s*/?\s*T\s*(\d{4,5})\s*[-—－]\s*(\d{4})\b", value, re.IGNORECASE)
    if not match:
        return None
    return f"GB/T {match.group(1)}-{match.group(2)}"


def compact_text(value: str) -> str:
    value = re.sub(r"\.[A-Za-z0-9]+$", "", value)
    value = value.replace("：", ":")
    value = re.sub(r"\s+", "", value)
    return value


def normalize_body_text(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("“", "\"").replace("”", "\"")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}

    end_index = text.find("\n---", 4)
    if end_index == -1:
        return {}

    metadata: dict[str, str] = {}
    for raw_line in text[4:end_index].splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest().upper()


def read_docx_paragraphs(path: Path) -> list[str]:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))

    paragraphs: list[str] = []
    text_tag = f"{{{namespace['w']}}}t"
    tab_tag = f"{{{namespace['w']}}}tab"
    break_tag = f"{{{namespace['w']}}}br"
    for paragraph in document.findall(".//w:p", namespace):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == text_tag and node.text:
                parts.append(node.text)
            elif node.tag == tab_tag:
                parts.append(" ")
            elif node.tag == break_tag:
                parts.append("\n")
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def clause_heading_from_text(text: str) -> tuple[str, str] | None:
    normalized = re.sub(r"\s+", " ", text).strip()
    match = re.match(r"^(10|[1-9])(?:\.(?:[1-9]\d*))*\s+(.+?)$", normalized)
    if not match:
        return None

    number_match = re.match(r"^(10|[1-9])(?:\.(?:[1-9]\d*))*", normalized)
    if not number_match:
        return None
    number = number_match.group(0)
    title = normalized[number_match.end() :].strip()
    if not title or re.fullmatch(r"\d+", title):
        return None
    if title[0] in {")", "）", ".", "。", "、"}:
        return None
    return number, title


def clause_number_only(text: str) -> str | None:
    normalized = re.sub(r"\s+", "", text)
    if re.fullmatch(r"(10|[1-9])(?:\.(?:[1-9]\d*))*", normalized):
        return normalized
    return None


def extract_clauses_from_docx(
    path: Path, standard_name: str, allowed_numbers: set[str] | None = None
) -> list[dict[str, str]]:
    paragraphs = read_docx_paragraphs(path)
    compact_standard_name = compact_text(standard_name)
    title_index = -1
    for index, paragraph in enumerate(paragraphs):
        if compact_standard_name and compact_standard_name in compact_text(paragraph):
            title_index = index

    candidates: list[tuple[int, str, str]] = []
    index = max(title_index + 1, 0)
    while index < len(paragraphs):
        paragraph = paragraphs[index]
        heading = clause_heading_from_text(paragraph)
        if heading:
            number, title = heading
            candidates.append((index, number, title))
            index += 1
            continue

        number_only = clause_number_only(paragraph)
        if number_only and index + 1 < len(paragraphs):
            next_text = paragraphs[index + 1].strip()
            if next_text and not clause_number_only(next_text) and not clause_heading_from_text(next_text):
                candidates.append((index, number_only, next_text))
                index += 2
                continue
        index += 1

    if not candidates:
        return []

    start = 0
    for pos, (_, number, title) in enumerate(candidates):
        if number == "1" and "范围" in title:
            start = pos
            break
    candidates = candidates[start:]
    for pos, (_, number, _) in enumerate(candidates):
        if number == "10.3":
            candidates = candidates[: pos + 1]
            break
    if allowed_numbers:
        candidates = [candidate for candidate in candidates if candidate[1] in allowed_numbers]

    clauses: list[dict[str, str]] = []
    for pos, (paragraph_index, number, title) in enumerate(candidates):
        next_index = candidates[pos + 1][0] if pos + 1 < len(candidates) else len(paragraphs)
        body_start = paragraph_index + (2 if paragraphs[paragraph_index].strip() == number else 1)
        body_lines = paragraphs[body_start:next_index]
        body = "\n".join(line for line in body_lines if line.strip())
        clauses.append({"number": number, "title": title.strip(), "body": body.strip()})
    return clauses


def parse_structured_clauses(text: str) -> dict[str, dict[str, Any]]:
    pattern = re.compile(r"(?m)^##\s+(\d+(?:\.\d+)*)\s+(.+?)\s*$")
    matches = list(pattern.finditer(text))
    clauses: dict[str, dict[str, Any]] = {}
    for index, match in enumerate(matches):
        number = match.group(1)
        title = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        fields: dict[str, str] = {}
        for line in block.splitlines():
            field_match = re.match(r"^-\s*([^：:]+)[：:]\s*(.*)$", line.strip())
            if field_match:
                fields[field_match.group(1).strip()] = field_match.group(2).strip()

        body = ""
        marker = re.search(r"(?m)^###\s+条款原文\s*$", block)
        if marker:
            body = block[marker.end() :].strip()
        clauses[number] = {"number": number, "title": title, "fields": fields, "body": body}
    return clauses


def format_frontmatter(metadata: dict[str, str], source_path: Path, source_hash: str) -> str:
    updated = dict(metadata)
    updated["source_sha256"] = source_hash
    updated["source_size_bytes"] = str(source_path.stat().st_size)
    updated["source_last_modified"] = dt.datetime.fromtimestamp(source_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    updated["draft_generated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updated["draft_status"] = "pending_review"
    if "source_path" in updated:
        del updated["source_path"]

    preferred_order = [
        "knowledge_base",
        "kb_folder",
        "source_folder",
        "standard_no",
        "system",
        "standard_name",
        "source_file",
        "source_kb_path",
        "source_sha256",
        "source_size_bytes",
        "source_last_modified",
        "official_status_url",
        "official_openstd_url",
        "official_text_availability",
        "structured_level",
        "generated_date",
        "draft_generated_at",
        "draft_status",
    ]
    lines = ["---"]
    emitted: set[str] = set()
    for key in preferred_order:
        if key in updated and updated[key] != "":
            value = updated[key]
            if key.endswith("_at") or key.endswith("_modified"):
                value = f'"{value}"'
            lines.append(f"{key}: {value}")
            emitted.add(key)
    for key in sorted(set(updated) - emitted):
        lines.append(f"{key}: {updated[key]}")
    lines.append("---")
    return "\n".join(lines)


def build_structured_draft(structured_path: Path, source_path: Path) -> tuple[str, dict[str, Any]]:
    old_text = structured_path.read_text(encoding="utf-8-sig")
    metadata = parse_frontmatter(old_text)
    standard_no = metadata.get("standard_no") or normalize_standard_no(structured_path.name) or ""
    standard_name = metadata.get("standard_name") or ""
    system_name = metadata.get("system") or ""
    knowledge_base = metadata.get("knowledge_base") or ""
    source_hash = hash_file(source_path)

    old_clauses = parse_structured_clauses(old_text)
    new_clauses = extract_clauses_from_docx(source_path, standard_name, set(old_clauses) or None)
    if not new_clauses:
        raise ValueError(f"未能从原标准文件提取条款: {source_path}")

    output_lines = [
        format_frontmatter(metadata, source_path, source_hash),
        "",
        f"# {standard_no} {standard_name}".strip(),
        "",
        "说明：本文件为自动生成的结构化条款草稿，仅用于人工复核后替换正式版本；不会自动覆盖知识库中的正式文件。",
        "",
    ]

    changed = 0
    added = 0
    unchanged = 0
    for clause in new_clauses:
        number = clause["number"]
        title = clause["title"]
        body = clause["body"]
        old_clause = old_clauses.get(number)
        old_fields = old_clause.get("fields", {}) if old_clause else {}
        old_body = old_clause.get("body", "") if old_clause else ""
        if not old_clause:
            review_status = "new_clause"
            added += 1
        elif normalize_body_text(body) == normalize_body_text(old_body):
            review_status = "ok"
            unchanged += 1
        else:
            review_status = "needs_review"
            changed += 1

        output_lines.extend(
            [
                f"## {number} {title}",
                "",
                f"- 知识库：{knowledge_base or old_fields.get('知识库')}",
                f"- 体系：{old_fields.get('体系') or system_name}",
                f"- 标准编号：{old_fields.get('标准编号') or standard_no}",
                f"- 标准名称：{old_fields.get('标准名称') or standard_name}",
                f"- 条款号：{number}",
                f"- 条款标题：{title}",
                f"- 关键词：{old_fields.get('关键词') or f'{system_name}、{standard_no}、{title}'}",
                f"- 条款关联：{old_fields.get('条款关联') or '待补充'}",
                f"- 标准含义：{old_fields.get('标准含义') or '待基于条款原文和审核场景补充'}",
                f"- 审核意图：{old_fields.get('审核意图') or '待基于审核目的补充'}",
                f"- 复核状态：{review_status}",
                "",
                "### 条款原文",
                body or "待复核：未能从原标准文件抽取到条款正文。",
                "",
            ]
        )

    summary = {
        "source": str(source_path),
        "source_sha256": source_hash,
        "clauses": len(new_clauses),
        "unchanged": unchanged,
        "changed": changed,
        "added": added,
    }
    return "\n".join(output_lines).rstrip() + "\n", summary


def default_output_dir() -> Path:
    default_path = Path("/home/gem/user-data/outputs")
    if default_path.exists() or default_path.parent.exists():
        return default_path
    return Path("outputs")


def rebuild_drafts(
    structured_checks: list[dict[str, Any]], output_dir: Path, *, force: bool = False
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    drafts: list[dict[str, Any]] = []
    for item in structured_checks:
        source_path = item.get("source_path")
        if not source_path:
            continue
        if item.get("hash_matches") and not force:
            continue

        structured_path = Path(str(item["file"]))
        try:
            content, summary = build_structured_draft(structured_path, Path(str(source_path)))
            draft_path = output_dir / f"{structured_path.stem}_draft_{timestamp}.md"
            draft_path.write_text(content, encoding="utf-8")
            drafts.append(
                {
                    "file": str(structured_path),
                    "draft_path": str(draft_path),
                    "success": True,
                    **summary,
                }
            )
        except Exception as exc:
            drafts.append(
                {
                    "file": str(structured_path),
                    "draft_path": None,
                    "success": False,
                    "error": str(exc),
                }
            )
    return drafts


def collect_structured_files(files: list[str], dirs: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    def add_path(path: Path) -> None:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    for file_name in files:
        add_path(Path(file_name))
    for dir_name in dirs:
        directory = Path(dir_name)
        if not directory.exists() or not directory.is_dir():
            add_path(directory)
            continue
        for file_path in sorted(directory.glob("*.md")):
            add_path(file_path)
    return paths


def resolve_source_path(metadata: dict[str, str], source_roots: list[Path]) -> Path | None:
    source_kb_path = metadata.get("source_kb_path")
    if source_kb_path:
        relative_path = Path(source_kb_path)
        for root in source_roots:
            candidates = [root / relative_path]
            if len(relative_path.parts) > 1:
                candidates.append(root / Path(*relative_path.parts[1:]))
            for candidate in candidates:
                if candidate.exists():
                    return candidate

    raw_source_path = metadata.get("source_path")
    if raw_source_path:
        source_path = Path(raw_source_path)
        if source_path.exists():
            return source_path

    source_file = metadata.get("source_file")
    if not source_file:
        return None

    for root in source_roots:
        candidate = root / source_file
        if candidate.exists():
            return candidate
    return None


def check_structured_files(
    structured_files: list[str], structured_dirs: list[str], source_roots: list[str]
) -> list[dict[str, Any]]:
    roots = [Path(item) for item in source_roots]
    known_standards = {item.standard_no for item in STANDARDS}
    checks: list[dict[str, Any]] = []

    for path in collect_structured_files(structured_files, structured_dirs):
        if not path.exists():
            checks.append(
                {
                    "file": str(path),
                    "level": "error",
                    "messages": ["结构化条款文件或目录不存在。"],
                    "hash_matches": False,
                }
            )
            continue
        if path.is_dir():
            checks.append(
                {
                    "file": str(path),
                    "level": "error",
                    "messages": ["结构化条款目录不存在或不是可扫描的 Markdown 文件目录。"],
                    "hash_matches": False,
                }
            )
            continue

        text = path.read_text(encoding="utf-8-sig")
        metadata = parse_frontmatter(text)
        standard_no = metadata.get("standard_no") or normalize_standard_no(path.name)
        source_path = resolve_source_path(metadata, roots + [path.parent])
        expected_hash = (metadata.get("source_sha256") or "").upper()
        clause_headings = len(re.findall(r"(?m)^##\s+\d+(?:\.\d+)*\s+", text))
        body_markers = len(re.findall(r"(?m)^###\s+条款原文\s*$", text))

        messages: list[str] = []
        hash_matches = False
        actual_hash: str | None = None
        source_size: int | None = None

        if not metadata:
            messages.append("缺少 YAML frontmatter。")
        if not standard_no:
            messages.append("缺少或无法识别 standard_no。")
        elif standard_no not in known_standards:
            messages.append(f"standard_no 不在三体系标准清单中: {standard_no}")
        if not expected_hash:
            messages.append("缺少 source_sha256。")
        if source_path is None:
            messages.append("未找到 source_kb_path/source_file 对应的原标准文件。")
        else:
            actual_hash = hash_file(source_path)
            source_size = source_path.stat().st_size
            hash_matches = bool(expected_hash and expected_hash == actual_hash)
            if expected_hash and not hash_matches:
                messages.append("source_sha256 与当前原标准文件不一致，需要复核或重构结构化条款。")

            recorded_size = metadata.get("source_size_bytes")
            if recorded_size and recorded_size.isdigit() and int(recorded_size) != source_size:
                messages.append("source_size_bytes 与当前原标准文件大小不一致。")

        if clause_headings == 0:
            messages.append("未识别到结构化条款标题。")
        if body_markers == 0:
            messages.append("未识别到“### 条款原文”标记。")
        if clause_headings and body_markers and clause_headings != body_markers:
            messages.append("条款标题数量与“### 条款原文”标记数量不一致。")

        level = "ok" if not messages else "warning"
        if any("不一致" in message or "未找到" in message or "缺少" in message for message in messages):
            level = "error"

        checks.append(
            {
                "file": str(path),
                "standard_no": standard_no,
                "source_file": metadata.get("source_file"),
                "source_kb_path": metadata.get("source_kb_path"),
                "source_path": str(source_path) if source_path else None,
                "expected_source_sha256": expected_hash or None,
                "actual_source_sha256": actual_hash,
                "hash_matches": hash_matches,
                "source_size_bytes": source_size,
                "clause_headings": clause_headings,
                "clause_body_markers": body_markers,
                "level": level,
                "messages": messages or ["结构化条款与当前原标准文件指纹一致，条款结构检查通过。"],
            }
        )

    return checks


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def fetch_official_page(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(content_type, errors="replace")


def extract_field(text: str, label: str) -> str | None:
    next_labels = (
        "标准号|发布日期|实施日期|上次复审日期|上次复审结论|全部代替标准|"
        "标准类别|中国标准分类号|国际标准分类号|归口单位|执行单位|主管部门|采标情况|$"
    )
    pattern = rf"{re.escape(label)}\s*:?\s*([^:：]+?)(?=\s+(?:{next_labels}))"
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(1).strip(" ;,，")


def parse_revision_plans(text: str) -> list[str]:
    marker = "修订计划"
    if marker not in text:
        return []

    section = text.split(marker, 1)[1]
    section = re.split(r"\s+基础信息\s+", section, maxsplit=1)[0]
    if "相近标准" in section:
        section = section.split("相近标准", 1)[0]

    plans: list[str] = []
    plan_pattern = r"(\d{8}-T-\d{3})\s+([^。；;]+?)(?=\s+\d{8}-T-\d{3}|\s+GB/T|\s+基础信息|\s+相近标准|$)"
    for match in re.finditer(plan_pattern, section):
        item = " ".join(match.group(0).split())
        if item not in plans:
            plans.append(item)
    return plans


def parse_official_status(spec: StandardSpec, timeout: int) -> dict[str, Any]:
    html_text = fetch_official_page(spec.source_url, timeout)
    text = strip_html(html_text)

    status = "未知"
    status_match = re.search(rf"{re.escape(spec.standard_no)}\s*(现行|废止|即将实施)", text)
    if status_match:
        status = status_match.group(1)
    elif re.search(r"国家标准\s+推荐性\s+现行", text):
        status = "现行"

    revision_plans = parse_revision_plans(text)
    return {
        "standard_no": spec.standard_no,
        "official_title": spec.official_title,
        "status": status,
        "release_date": extract_field(text, "发布日期"),
        "implementation_date": extract_field(text, "实施日期"),
        "review_date": extract_field(text, "上次复审日期"),
        "review_conclusion": extract_field(text, "上次复审结论"),
        "replaced_standards": extract_field(text, "全部代替标准"),
        "has_revision_plan": bool(revision_plans),
        "revision_plans": revision_plans,
        "source_url": spec.source_url,
        "official_checked": True,
    }


def check_local_names(names: list[str]) -> list[dict[str, Any]]:
    known = {item.standard_no: item for item in STANDARDS}
    results: list[dict[str, Any]] = []
    for name in names:
        standard_no = normalize_standard_no(name)
        if not standard_no:
            results.append(
                {
                    "name": name,
                    "matched": False,
                    "level": "warning",
                    "message": "未识别到 GB/T 标准号，建议在名称中包含标准号。",
                }
            )
            continue

        spec = known.get(standard_no)
        if not spec:
            results.append(
                {
                    "name": name,
                    "matched": False,
                    "standard_no": standard_no,
                    "level": "warning",
                    "message": "识别到标准号，但不在本技能维护的三体系标准清单中。",
                }
            )
            continue

        normalized_name = compact_text(name)
        normalized_title = compact_text(spec.official_title)
        title_ok = normalized_title in normalized_name
        results.append(
            {
                "name": name,
                "matched": True,
                "standard_no": standard_no,
                "official_title": spec.official_title,
                "title_ok": title_ok,
                "level": "ok" if title_ok else "notice",
                "message": "标准号和官方名称匹配。" if title_ok else "标准号匹配，但名称未完整包含官方名称。",
            }
        )
    return results


def build_report(
    names: list[str],
    timeout: int,
    structured_files: list[str] | None = None,
    structured_dirs: list[str] | None = None,
    source_roots: list[str] | None = None,
    rebuild_draft: bool = False,
    force_rebuild_draft: bool = False,
    output_dir: str | None = None,
) -> dict[str, Any]:
    standards: list[dict[str, Any]] = []
    for spec in STANDARDS:
        try:
            standards.append(parse_official_status(spec, timeout))
        except (TimeoutError, urllib.error.URLError, OSError, UnicodeDecodeError) as exc:
            standards.append(
                {
                    "standard_no": spec.standard_no,
                    "official_title": spec.official_title,
                    "status": "未知",
                    "has_revision_plan": False,
                    "revision_plans": [],
                    "source_url": spec.source_url,
                    "official_checked": False,
                    "error": str(exc),
                }
            )

    structured_checks = check_structured_files(structured_files or [], structured_dirs or [], source_roots or [])
    drafts: list[dict[str, Any]] = []
    if rebuild_draft or force_rebuild_draft:
        drafts = rebuild_drafts(
            structured_checks,
            Path(output_dir) if output_dir else default_output_dir(),
            force=force_rebuild_draft,
        )

    return {
        "standards": standards,
        "local_name_checks": check_local_names(names),
        "structured_clause_checks": structured_checks,
        "drafts": drafts,
    }


def print_text_report(report: dict[str, Any]) -> None:
    print("三体系标准状态检查")
    print()
    for item in report["standards"]:
        checked = "实时核验" if item["official_checked"] else "未完成实时核验"
        print(f"- {item['standard_no']} {item['official_title']}")
        print(f"  状态: {item['status']} ({checked})")
        if item.get("review_date"):
            print(f"  上次复审: {item['review_date']} / {item.get('review_conclusion') or '未提取到结论'}")
        if item.get("release_date") or item.get("implementation_date"):
            print(f"  发布/实施: {item.get('release_date') or '-'} / {item.get('implementation_date') or '-'}")
        if item.get("revision_plans"):
            print(f"  修订计划: {'；'.join(item['revision_plans'])}")
        if item.get("error"):
            print(f"  错误: {item['error']}")
        print(f"  来源: {item['source_url']}")
        print()

    if report["local_name_checks"]:
        print("本地名称检查")
        print()
        for item in report["local_name_checks"]:
            prefix = "[OK]" if item["level"] == "ok" else "[注意]" if item["level"] == "notice" else "[警告]"
            print(f"- {prefix} {item['name']}")
            print(f"  {item['message']}")
            if item.get("official_title"):
                print(f"  官方名称: {item['standard_no']} {item['official_title']}")
            print()

    if report["structured_clause_checks"]:
        print("结构化条款一致性检查")
        print()
        for item in report["structured_clause_checks"]:
            prefix = "[OK]" if item["level"] == "ok" else "[需处理]" if item["level"] == "error" else "[注意]"
            print(f"- {prefix} {item['file']}")
            if item.get("standard_no"):
                print(f"  标准号: {item['standard_no']}")
            if item.get("source_path"):
                print(f"  原文件: {item['source_path']}")
            print(f"  原件 Hash 匹配: {'是' if item['hash_matches'] else '否'}")
            print(f"  条款标题/原文标记: {item['clause_headings']} / {item['clause_body_markers']}")
            for message in item["messages"]:
                print(f"  - {message}")
            print()

    if report["drafts"]:
        print("结构化条款草稿")
        print()
        for item in report["drafts"]:
            if item.get("success"):
                print(f"- [已生成] {item['draft_path']}")
                print(
                    "  条款统计: "
                    f"总数 {item.get('clauses', 0)} / "
                    f"未变 {item.get('unchanged', 0)} / "
                    f"需复核 {item.get('changed', 0)} / "
                    f"新增 {item.get('added', 0)}"
                )
            else:
                print(f"- [失败] {item['file']}")
                print(f"  错误: {item.get('error')}")
            print()


def main() -> int:
    parser = argparse.ArgumentParser(description="检查三体系 GB/T 标准官方状态并校验本地名称。")
    parser.add_argument("--name", action="append", default=[], help="本地知识库名或文件名，可重复传入。")
    parser.add_argument("--structured-file", action="append", default=[], help="结构化条款 Markdown 文件，可重复传入。")
    parser.add_argument("--structured-dir", action="append", default=[], help="结构化条款 Markdown 目录，可重复传入。")
    parser.add_argument("--source-root", action="append", default=[], help="原标准文件所在目录，用于按 source_file 兜底定位。")
    parser.add_argument("--rebuild-draft", action="store_true", help="结构化条款过期时生成草稿，不覆盖正式文件。")
    parser.add_argument("--force-rebuild-draft", action="store_true", help="即使结构化条款未过期也强制生成草稿，用于人工复核。")
    parser.add_argument("--output-dir", default=None, help="草稿输出目录，默认使用 /home/gem/user-data/outputs。")
    parser.add_argument("--timeout", type=int, default=20, help="访问官方页面的超时时间，单位秒。")
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    parser.add_argument("--strict", action="store_true", help="结构化条款检查存在问题时返回非 0 退出码。")
    args = parser.parse_args()

    report = build_report(
        args.name,
        args.timeout,
        structured_files=args.structured_file,
        structured_dirs=args.structured_dir,
        source_roots=args.source_root,
        rebuild_draft=args.rebuild_draft,
        force_rebuild_draft=args.force_rebuild_draft,
        output_dir=args.output_dir,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)

    if any(not item["official_checked"] for item in report["standards"]):
        return 2
    if args.strict and any(item["level"] == "error" for item in report["structured_clause_checks"]):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
