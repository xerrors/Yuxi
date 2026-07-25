from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.agents.skills.service import import_skill_dir, is_valid_skill_slug

if TYPE_CHECKING:
    from yuxi.storage.postgres.models_business import Skill

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
CONTROL_SEQUENCE_RE = re.compile(r"\x1B\][^\x07]*(?:\x07|\x1B\\)|\x1B[\(\)][A-Za-z0-9]")
CLI_TIMEOUT_SECONDS = 300
GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_ALLOWED_HOSTS = {"github.com", "www.github.com"}
INVALID_SOURCE_MESSAGE = "source 仅支持 GitHub owner/repo 或 https://github.com/OWNER/REPO(.git) 格式"


@dataclass(slots=True)
class RemoteSkillsBatchPreparation:
    temp_home: str | None
    results: list[dict]

    async def cleanup(self) -> None:
        if self.temp_home:
            await asyncio.to_thread(shutil.rmtree, self.temp_home, ignore_errors=True)


def _normalize_source(source: str) -> str:
    value = str(source or "").strip()
    if not value:
        raise ValueError("source 不能为空")
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise ValueError("source 包含非法字符")
    if GITHUB_REPO_PATTERN.fullmatch(value):
        return value

    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in GITHUB_ALLOWED_HOSTS:
        raise ValueError(INVALID_SOURCE_MESSAGE)
    if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
        raise ValueError(INVALID_SOURCE_MESSAGE)

    repo_path = parsed.path.strip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    if not GITHUB_REPO_PATTERN.fullmatch(repo_path):
        raise ValueError(INVALID_SOURCE_MESSAGE)

    owner, repo = repo_path.split("/", 1)
    return f"https://github.com/{owner}/{repo}"


def _normalize_skill_name(skill: str) -> str:
    value = str(skill or "").strip()
    if not is_valid_skill_slug(value):
        raise ValueError("skill 名称不合法")
    return value


def _clean_cli_output(output: str) -> list[str]:
    cleaned = ANSI_ESCAPE_RE.sub("", output or "")
    cleaned = CONTROL_SEQUENCE_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r", "\n")
    normalized_lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        stripped = re.sub(r"^[│┌└◇◒◐◓◑■●]+\s*", "", stripped)
        normalized_lines.append(stripped.strip())
    return normalized_lines


def _parse_available_skills(output: str) -> list[dict[str, str]]:
    lines = _clean_cli_output(output)
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    collecting = False

    for idx, line in enumerate(lines):
        if not collecting:
            if "Available Skills" in line:
                collecting = True
            continue

        if not line:
            continue
        if "Use --skill " in line:
            break
        if not is_valid_skill_slug(line):
            continue
        if line in seen:
            continue

        description = ""
        next_index = idx + 1
        while next_index < len(lines):
            next_line = lines[next_index]
            next_index += 1
            if not next_line:
                continue
            if "Use --skill " in next_line:
                break
            if is_valid_skill_slug(next_line):
                break
            if next_line and next_line[0].isalpha():
                description = next_line
            else:
                continue
            break

        seen.add(line)
        items.append({"name": line, "description": description})

    return items


async def _run_skills_cli(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: str,
) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=CLI_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise ValueError("skills CLI 执行超时") from None

    output = (stdout or b"").decode("utf-8", errors="replace")
    error_output = (stderr or b"").decode("utf-8", errors="replace")
    combined = "\n".join(part for part in [output.strip(), error_output.strip()] if part)
    if process.returncode != 0:
        cleaned_lines = _clean_cli_output(combined)
        error_msg = "\n".join(line for line in cleaned_lines if line)[:500]
        raise ValueError(error_msg or "skills CLI 执行失败")
    return combined


def _create_isolated_workdir() -> tuple[str, dict[str, str], str]:
    temp_home = tempfile.mkdtemp(prefix=".remote-skills-")
    env = os.environ.copy()
    env["HOME"] = temp_home
    workdir = str(Path(temp_home) / "workspace")
    Path(workdir).mkdir(parents=True, exist_ok=True)
    return temp_home, env, workdir


async def list_remote_skills(source: str) -> list[dict[str, str]]:
    normalized_source = _normalize_source(source)

    temp_home, env, workdir = _create_isolated_workdir()
    try:
        output = await _run_skills_cli(
            ["npx", "-y", "skills", "add", normalized_source, "--list"],
            env=env,
            cwd=workdir,
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, temp_home, ignore_errors=True)

    skills = _parse_available_skills(output)
    if not skills:
        raise ValueError("未发现可安装的 skills")
    return skills


async def install_remote_skill(
    db: AsyncSession,
    *,
    source: str,
    skill: str,
    created_by: str | None,
) -> Skill:
    normalized_source = _normalize_source(source)
    normalized_skill = _normalize_skill_name(skill)

    temp_home, env, workdir = _create_isolated_workdir()
    try:
        available_skills = _parse_available_skills(
            await _run_skills_cli(
                ["npx", "-y", "skills", "add", normalized_source, "--list"],
                env=env,
                cwd=workdir,
            )
        )
        available_names = {item["name"] for item in available_skills}
        if normalized_skill not in available_names:
            raise ValueError(f"远程仓库中不存在 skill: {normalized_skill}")

        await _run_skills_cli(
            [
                "npx",
                "-y",
                "skills",
                "add",
                normalized_source,
                "--skill",
                normalized_skill,
                "-g",
                "-y",
                "--copy",
            ],
            env=env,
            cwd=workdir,
        )

        skills_dir = Path(temp_home).resolve() / ".agents" / "skills"
        installed_dir = _find_skill_dir(skills_dir, normalized_skill)
        if installed_dir is None:
            raise ValueError("skills CLI 未生成预期的技能目录")

        return await import_skill_dir(
            db,
            source_dir=installed_dir,
            created_by=created_by,
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, temp_home, ignore_errors=True)


async def install_remote_skills_batch(
    db: AsyncSession,
    *,
    source: str,
    skills: list[str],
    created_by: str | None,
) -> list[dict]:
    """批量从同一个远程仓库安装多个 skills（仅一次克隆）。

    Args:
        db: 数据库会话。
        source: 远程仓库来源，如 ``owner/repo`` 或 GitHub URL。
        skills: 需要安装的 skill 名称列表。
        created_by: 操作者标识。

    Returns:
        每个 skill 的安装结果列表，顺序与请求一致: ``[{slug, success, error?}, ...]``
    """
    preparation = await prepare_remote_skills_batch(source=source, skills=skills)
    try:
        results = preparation.results
        for index, result in enumerate(results):
            if not result.get("success"):
                continue

            source_dir = result.get("source_dir")
            try:
                item = await import_skill_dir(
                    db,
                    source_dir=source_dir,
                    created_by=created_by,
                )
                results[index] = {"slug": item.slug, "success": True}
            except Exception as e:
                if hasattr(db, "rollback"):
                    await db.rollback()
                results[index] = {"slug": result["slug"], "success": False, "error": str(e)}

        return results
    finally:
        await preparation.cleanup()


async def prepare_remote_skills_batch(
    *,
    source: str,
    skills: list[str],
) -> RemoteSkillsBatchPreparation:
    """批量从远程仓库拉取 skill 目录，但不写数据库。"""
    normalized_source = _normalize_source(source)
    if not skills:
        raise ValueError("skills 列表不能为空")

    # 预分配结果数组（按请求顺序），校验非法名并记录失败
    results: list[dict] = [{"slug": "", "success": False, "error": "unset"} for _ in range(len(skills))]
    normalized_skills: list[str] = []
    valid_indices: list[int] = []
    for i, skill in enumerate(skills):
        try:
            normalized_skills.append(_normalize_skill_name(skill))
            valid_indices.append(i)
        except ValueError as e:
            results[i] = {"slug": skill, "success": False, "error": str(e)}

    if not normalized_skills:
        return RemoteSkillsBatchPreparation(temp_home=None, results=results)

    temp_home, env, workdir = _create_isolated_workdir()
    try:
        skill_args: list[str] = []
        for name in normalized_skills:
            skill_args.extend(["--skill", name])

        cli_failed = False
        try:
            await _run_skills_cli(
                [
                    "npx",
                    "-y",
                    "skills",
                    "add",
                    normalized_source,
                    *skill_args,
                    "-g",
                    "-y",
                    "--copy",
                ],
                env=env,
                cwd=workdir,
            )
        except ValueError:
            # CLI 对不匹配的 skill 会退出码非零，但已安装的目录仍在
            cli_failed = True

        skills_dir = Path(temp_home).resolve() / ".agents" / "skills"
        for original_index, name in zip(valid_indices, normalized_skills):
            installed_dir = _find_skill_dir(skills_dir, name)
            if installed_dir is None:
                error_msg = "CLI 安装失败" if cli_failed else "skills CLI 未生成预期的技能目录"
                results[original_index] = {"slug": name, "success": False, "error": error_msg}
            else:
                results[original_index] = {"slug": name, "success": True, "source_dir": installed_dir}

        return RemoteSkillsBatchPreparation(temp_home=temp_home, results=results)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, temp_home, ignore_errors=True)
        raise


def _find_skill_dir(skills_dir: Path, name: str) -> Path | None:
    """在 skills 安装目录下按名称查找 skill 子目录。"""
    if not skills_dir.is_dir():
        return None
    for candidate in skills_dir.iterdir():
        if candidate.name == name and candidate.is_dir():
            return candidate
    return None


def _parse_search_skills(output: str) -> list[dict[str, str]]:
    """解析 npx skills find 命令的输出。"""
    lines = _clean_cli_output(output)
    results: list[dict[str, str]] = []
    # 匹配形如 "owner/repo@skill-name [installs]"
    # 例如：vercel-labs/agent-skills@web-design-guidelines 339.3K installs
    pattern = re.compile(r"^([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+)\@([a-zA-Z0-9_\-\.]+)(?:\s+(.*))?$")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if match:
            source, name, extra = match.groups()
            installs = extra.strip() if extra else ""
            results.append(
                {
                    "source": source,
                    "name": name,
                    "installs": installs,
                }
            )
    return results


async def search_remote_skills(query: str) -> list[dict[str, str]]:
    """使用 npx skills find <query> 搜索远程 skills。"""
    query_val = str(query or "").strip()
    if not query_val:
        return []
    if any(ch in query_val for ch in ("\n", "\r", "\x00")):
        raise ValueError("搜索关键字包含非法字符")

    temp_home, env, workdir = _create_isolated_workdir()
    try:
        output = await _run_skills_cli(
            ["npx", "-y", "skills", "find", query_val],
            env=env,
            cwd=workdir,
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, temp_home, ignore_errors=True)

    return _parse_search_skills(output)
