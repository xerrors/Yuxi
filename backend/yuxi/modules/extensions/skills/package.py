"""Skill 包格式与安全复制原语。"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pypinyin import Style, lazy_pinyin

from yuxi.infrastructure.filesystem import open_directory_fd, open_regular_file_fd

MAX_SKILL_BYTES = 50 * 1024 * 1024
MAX_SKILL_ENTRIES = 2000

SKILL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

TEXT_FILE_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".html",
    ".css",
    ".sql",
    ".sh",
    ".bat",
    ".ps1",
    ".env",
    ".csv",
    ".tsv",
    ".rst",
    ".ipynb",
    ".vue",
    ".jsx",
    ".tsx",
}


class SkillEditConflict(ValueError):
    """读取后的 Skill 内容已被其他提交更新。"""


def parse_skill_dir_metadata(source_skill_dir: Path) -> dict[str, Any]:
    """读取目录中的根文件并提取 Skill 元数据。"""
    skill_md_path = source_skill_dir / "SKILL.md"
    if not skill_md_path.is_file():
        raise ValueError("技能目录缺少根级 SKILL.md")

    content = skill_md_path.read_text(encoding="utf-8")
    parsed_slug, parsed_name, parsed_desc, meta = parse_skill_markdown(content)
    return {
        "slug": parsed_slug,
        "name": parsed_name,
        "description": parsed_desc,
    }


def parse_skill_markdown(content: str) -> tuple[str, str, str, dict[str, Any]]:
    """解析并校验 Skill 根文件的前置元数据。"""
    frontmatter_raw, _body = split_skill_frontmatter(content)
    data = load_skill_frontmatter(frontmatter_raw)
    if any(key in data for key in ("tool_dependencies", "mcp_dependencies", "skill_dependencies")):
        raise ValueError("普通 Skill 不再支持运行依赖，请将能力声明迁移到部署插件")

    name = _validate_skill_display_name(str(data.get("name", "")))
    raw_slug = str(data.get("slug", "")).strip()
    if raw_slug:
        slug = _validate_skill_slug_value(raw_slug, field_name="slug")
    elif is_valid_skill_slug(name):
        slug = name
    else:
        slug = derive_skill_slug_from_display_name(name)
    description = str(data.get("description", "")).strip()
    if not description:
        raise ValueError("SKILL.md frontmatter 缺少 description")

    return slug, name, description, data


def derive_skill_slug_from_display_name(name: str) -> str:
    """把展示名转为稳定标识，无可用字符时使用名称摘要。"""
    phonetic = "".join(lazy_pinyin(name, style=Style.NORMAL))
    slug = re.sub(r"[^a-z0-9]+", "-", phonetic.lower()).strip("-")[:64].rstrip("-")
    if slug:
        return slug
    return f"skill-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}"


def copy_skill_snapshot(
    source_dir: Path,
    target_dir: Path,
    *,
    expected_slug: str | None = None,
    final_slug: str | None = None,
) -> dict[str, Any]:
    """复制并解析 Skill staging，可校验来源或重写最终 slug。"""
    copy_skill_tree_no_symlinks(source_dir, target_dir)
    parsed = parse_skill_dir_metadata(target_dir)
    if expected_slug and parsed["slug"] != expected_slug:
        raise ValueError("Skill slug 在复制过程中发生变化")
    if final_slug is None and not is_valid_skill_slug(parsed["name"]):
        final_slug = parsed["slug"]
    if final_slug:
        skill_md = target_dir / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        _slug, _name, _description, metadata = parse_skill_markdown(content)
        if parsed["slug"] != final_slug or (not metadata.get("slug") and not is_valid_skill_slug(parsed["name"])):
            skill_md.write_text(rewrite_frontmatter_slug(content, final_slug), encoding="utf-8")
            parsed = parse_skill_dir_metadata(target_dir)
    return parsed


def copy_skill_tree_no_symlinks(source_dir: Path, target_dir: Path) -> None:
    """复制不含符号链接的 Skill 目录到 staging。"""
    if source_dir.is_symlink():
        raise ValueError("Skill 来源只允许普通文件和目录")
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    if skill_tree_contains_symlink(source_dir):
        raise ValueError("Skill 来源只允许普通文件和目录")
    try:
        shutil.copytree(source_dir, target_dir, symlinks=False)
    except BaseException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


def rewrite_frontmatter_slug(content: str, new_slug: str) -> str:
    """在安装快照中写入最终 Skill slug。"""
    frontmatter_raw, body = split_skill_frontmatter(content)
    data = load_skill_frontmatter(frontmatter_raw)
    had_explicit_slug = bool(str(data.get("slug", "")).strip())
    name = str(data.get("name", "")).strip()
    data["slug"] = new_slug
    # 旧包的 name 同时充当标识；自然语言展示名不随同名安装改写。
    if not had_explicit_slug and is_valid_skill_slug(name):
        data["name"] = new_slug
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{dumped}\n---\n{body}"


def validated_skill_file_parts(relative_path: str, *, error_message: str = "非法 Skill 文件路径") -> tuple[str, ...]:
    """校验 Skill 根下的相对文件路径并返回组件。"""
    if not relative_path or relative_path.startswith("/") or "\\" in relative_path:
        raise ValueError(error_message)
    parts = PurePosixPath(relative_path).parts
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError(error_message)
    return parts


def validated_shared_skill_parts(slug: str, dir_path: str) -> tuple[str, ...]:
    """确认索引只指向自身 Skill 的内置目录或不可变内容。"""
    if not is_valid_skill_slug(slug):
        raise ValueError("Skill 来源目录非法")
    if dir_path == f"shared/{slug}":
        return "shared", slug
    parts = dir_path.split("/")
    if len(parts) == 3 and parts[:2] == ["packages", slug] and re.fullmatch(r"[0-9a-f]{32}", parts[2]):
        return tuple(parts)
    raise ValueError("Skill 来源目录非法")


def split_skill_frontmatter(content: str) -> tuple[str, str]:
    """拆分 Skill 根文件的前置元数据与正文。"""
    lines = content.splitlines(keepends=True)
    if not content.startswith("---") or not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    raise ValueError("SKILL.md 缺少有效 frontmatter（--- ... ---）")


def normalize_string_list(values: list[str] | None) -> list[str]:
    """去除字符串列表中的空值和重复项。"""
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def is_valid_skill_slug(slug: str) -> bool:
    """判断名称是否符合 Skill slug 格式。"""
    if not isinstance(slug, str):
        return False
    return SKILL_SLUG_PATTERN.fullmatch(slug) is not None


def skill_tree_contains_symlink(path: Path) -> bool:
    """检查目录内是否包含任意符号链接子路径。"""
    return any(child.is_symlink() for child in path.rglob("*"))


def load_skill_frontmatter(raw: str) -> dict[str, Any]:
    """兼容未引用且含冒号的多行 description。"""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        folded = re.sub(
            r"(?m)^description:[ \t]*(\r?\n)(?=[ \t]+\S)",
            lambda match: f"description: >-{match.group(1)}",
            raw,
            count=1,
        )
        if folded == raw:
            raise ValueError("SKILL.md frontmatter YAML 解析失败，请检查 YAML 语法") from error
        try:
            data = yaml.safe_load(folded)
        except yaml.YAMLError as folded_error:
            raise ValueError("SKILL.md frontmatter YAML 解析失败，请检查 YAML 语法") from folded_error
    if not isinstance(data, dict):
        raise ValueError("SKILL.md frontmatter 必须是对象")
    return data


def _validate_skill_slug_value(slug: str, *, field_name: str) -> str:
    """校验根文件中用于生成 slug 的值。"""
    slug = slug.strip()
    if not slug:
        raise ValueError(f"SKILL.md frontmatter 缺少 {field_name}")
    if len(slug) > 128:
        raise ValueError(f"SKILL.md frontmatter.{field_name} 长度不能超过 128")
    if not SKILL_SLUG_PATTERN.match(slug):
        raise ValueError(f"SKILL.md frontmatter.{field_name} 必须是小写字母/数字/短横线，且不能连续短横线")
    return slug


def _validate_skill_display_name(name: str) -> str:
    """校验根文件中的显示名称。"""
    name = name.strip()
    if not name:
        raise ValueError("SKILL.md frontmatter 缺少 name")
    if len(name) > 128:
        raise ValueError("SKILL.md frontmatter.name 长度不能超过 128")
    return name


def compute_skill_directory_hash(path: Path) -> bytes:
    """通过目录 fd 读取投影比较摘要，拒绝链接和特殊文件。"""
    hasher = hashlib.sha256()

    def visit(directory_fd: int) -> None:
        """在已打开的目录内递归比较所需的类型、执行位和字节。"""
        for name in sorted(os.listdir(directory_fd)):
            hasher.update(os.fsencode(name) + b"\0")
            mode = os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode
            if stat.S_ISDIR(mode):
                child_fd = open_directory_fd(directory_fd, (name,))
                try:
                    hasher.update(b"directory\0")
                    visit(child_fd)
                    hasher.update(b"end-directory\0")
                finally:
                    os.close(child_fd)
            else:
                with open_regular_file_fd(directory_fd, (name,)) as (file_fd, file_stat):
                    hasher.update(b"file\0" + bytes([stat.S_IMODE(file_stat.st_mode) & 0o111]))
                    content_hash = hashlib.sha256()
                    while chunk := os.read(file_fd, 1024 * 1024):
                        content_hash.update(chunk)
                    hasher.update(content_hash.digest())

    absolute = Path(os.path.abspath(path))
    directory_fd = open_directory_fd(Path(absolute.anchor), absolute.parts[1:])
    try:
        visit(directory_fd)
    finally:
        os.close(directory_fd)
    return hasher.digest()


def validate_content_budget(target: Path) -> None:
    """内容预算限制在完整包边界执行，避免在线编辑绕过上传限制。"""
    entries = list(target.rglob("*"))
    if len(entries) > MAX_SKILL_ENTRIES or sum(entry.stat().st_size for entry in entries if entry.is_file()) > MAX_SKILL_BYTES:
        raise ValueError("Skill 内容不能超过 50 MiB 或 2000 个条目")
