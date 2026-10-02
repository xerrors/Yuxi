"""Skill 包格式与安全复制原语。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

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
        "tool_dependencies": normalize_string_list(meta.get("tool_dependencies")),
        "mcp_dependencies": normalize_string_list(meta.get("mcp_dependencies")),
        "skill_dependencies": normalize_string_list(meta.get("skill_dependencies")),
    }


def parse_skill_markdown(content: str) -> tuple[str, str, str, dict[str, Any]]:
    """解析并校验 Skill 根文件的前置元数据。"""
    frontmatter_raw, _body = split_skill_frontmatter(content)
    data = _load_skill_frontmatter(frontmatter_raw)

    name = _validate_skill_display_name(str(data.get("name", "")))
    raw_slug = str(data.get("slug", "")).strip()
    slug = (
        _validate_skill_slug_value(raw_slug, field_name="slug")
        if raw_slug
        else _validate_skill_slug_value(name, field_name="name")
    )
    description = str(data.get("description", "")).strip()
    if not description:
        raise ValueError("SKILL.md frontmatter 缺少 description")

    return slug, name, description, data


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
    if final_slug and parsed["slug"] != final_slug:
        skill_md = target_dir / "SKILL.md"
        skill_md.write_text(
            rewrite_frontmatter_slug(skill_md.read_text(encoding="utf-8"), final_slug),
            encoding="utf-8",
        )
    return parsed


def copy_skill_tree_no_symlinks(source_dir: Path, target_dir: Path) -> None:
    """复制不含符号链接的 Skill 目录到 staging。"""
    if source_dir.is_symlink():
        raise ValueError(f"Skill 来源只允许普通文件和目录: {source_dir}")
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)
    if skill_tree_contains_symlink(source_dir):
        raise ValueError(f"Skill 来源只允许普通文件和目录: {source_dir}")
    try:
        shutil.copytree(source_dir, target_dir, symlinks=False)
    except BaseException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


def rewrite_frontmatter_slug(content: str, new_slug: str) -> str:
    """在安装快照中写入最终 Skill slug。"""
    frontmatter_raw, body = split_skill_frontmatter(content)
    data = _load_skill_frontmatter(frontmatter_raw)
    if data.get("slug"):
        data["slug"] = new_slug
    else:
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


def validated_shared_skill_parts(slug: str, dir_path: str) -> tuple[str, str]:
    """确认数据库共享来源只指向自身 slug 对应的持久目录。"""
    if not is_valid_skill_slug(slug) or dir_path != f"shared/{slug}":
        raise ValueError("Skill 来源目录非法")
    return "shared", slug


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


def _load_skill_frontmatter(raw: str) -> dict[str, Any]:
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
            raise ValueError(f"SKILL.md frontmatter YAML 解析失败: {error}") from error
        try:
            data = yaml.safe_load(folded)
        except yaml.YAMLError as folded_error:
            raise ValueError(f"SKILL.md frontmatter YAML 解析失败: {folded_error}") from folded_error
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
