"""从真实 Owner 派生工程信任检查，不维护中央主张清单。"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

DECISIONS_PATH = Path("docs/develop-guides/decisions")
POSTMORTEMS_PATH = Path("docs/develop-guides/postmortems")
FORBIDDEN_CENTRAL_INVENTORIES = (Path("docs/develop-guides/engineering-claims.json"),)
DECISION_FILE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*\.md$")
DECISION_TYPES = frozenset(
    {"feature", "bug-fix", "simplification", "architecture", "process", "testing"}
)
DECISION_REQUIRED_HEADINGS = {
    "implemented": ("## 问题", "## 决策", "## 替代方案", "## 后果", "## 验证"),
    "proposed": ("## 问题", "## 提案", "## 替代方案", "## 验收标准", "## 风险"),
    "rejected": ("## 问题", "## 提案", "## 替代方案", "## 拒绝理由"),
    "archived": ("## 问题", "## 决策", "## 替代方案", "## 后果", "## 验证"),
}
IMPLEMENTED_BANNED_HEADINGS = (
    "## 提案",
    "## 实施步骤",
    "## 迁移步骤",
    "## Checklist",
    "## 进度",
)
PROPOSED_EVIDENCE_HEADER = (
    "| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |"
)
EVIDENCE_RESULTS = frozenset({"Passed", "Inspected", "Not run", "Inferred"})
SIMPLIFICATION_REQUIRED_LABELS = ("旧能力不存在：", "重新引入条件：")
POSTMORTEM_TEMPLATE_HEADINGS = (
    "## 影响",
    "## 事实时间线",
    "## 因果链",
    "## 安全网为何漏过",
    "## 修正与验证",
    "## 防复发措施",
    "## 未解决风险",
)
ROUTER_DB_METHODS = frozenset(
    {
        "execute",
        "scalar",
        "scalars",
        "get",
        "add",
        "add_all",
        "delete",
        "flush",
        "merge",
    }
)
ROUTER_DB_RECEIVERS = frozenset({"db", "session", "connection", "database"})
WORKSPACE_HOST_PATH_EXPORTS = frozenset(
    {
        "global_user_data_dir",
        "user_workspace_dir",
        "user_workdir_host_dir",
    }
)
PERSONAL_SKILL_WORKSPACE_OWNER = Path(
    "backend/yuxi/modules/extensions/skills/personal.py"
)
DIRECT_FRONTEND_API_LITERAL = re.compile(r"(?P<quote>['\"`])/api(?:[/ ?]|(?P=quote))")
AGENTS_FILE_BUDGETS = {
    "AGENTS.md": 5000,
    "backend/AGENTS.md": 2400,
    "frontend/AGENTS.md": 1000,
    "docs/AGENTS.md": 3200,
}
AGENTS_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")
EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:")
DOCUMENT_PROSE_CONTRAST = re.compile(
    r"(?:不是|并非)[^。\n]{0,160}(?:而是|而在于)"
    r"|不在于[^。\n]{0,160}(?:而是|而在于)"
)
MARKDOWN_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
MARKDOWN_BLOCKQUOTE_PREFIX = re.compile(r"^ {0,3}> ?")
MARKDOWN_LIST_ITEM = re.compile(
    r"^(?P<indent> {0,3})(?P<marker>[-+*]|\d{1,9}[.)])(?P<spacing> {1,4})(?P<content>.*)$"
)


# 入口由各 workflow 拥有，具体 pytest 节点和参数不在这里复制。
WORKFLOW_ENTRIES = {
    "trust.yml": (
        "scripts/ci/verify_engineering_contracts.py",
        "scripts.ci.test_verify_engineering_contracts",
        "scripts/ci/test_init_security.ps1",
    ),
    "test.yml": ("pytest test/unit",),
    "frontend.yml": ("pnpm run lint:check", "pnpm run test:unit", "pnpm run build"),
    "service-integration.yml": ("scripts/ci/run_service_integration.sh",),
    "system-tests.yml": (
        "backend/test/run_tests.sh e2e-cooperation",
        "backend/test/run_tests.sh e2e-core",
        "test/integration/sandbox/test_container_sandbox.py",
    ),
    "real-provider-probe.yml": ("test/e2e/test_real_provider_e2e.py",),
}
CI_WORKFLOWS = {
    "trust": "trust.yml",
    "backend": "test.yml",
    "ruff": "ruff.yml",
    "frontend": "frontend.yml",
    "service": "service-integration.yml",
    "system": "system-tests.yml",
    "docs": "docs-check.yml",
    "dependencies": "dependency-audit.yml",
}


def _require_repository_path(
    root: Path,
    raw_path: object,
    label: str,
    errors: list[str],
    *,
    file_only: bool = False,
) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        errors.append(f"{label} 必须是非空仓库相对路径")
        return None
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        errors.append(f"{label} 必须位于仓库内：{raw_path}")
        return None
    resolved = root / path
    if not resolved.resolve().is_relative_to(root.resolve()):
        errors.append(f"{label} 不得通过符号链接逃逸仓库：{raw_path}")
        return None
    if not resolved.exists():
        errors.append(f"{label} 引用不存在：{raw_path}")
        return None
    if file_only and not resolved.is_file():
        errors.append(f"{label} 必须引用文件：{raw_path}")
        return None
    return resolved


def _normalized(text: str) -> str:
    return " ".join(text.replace("\\\n", " ").split())


def _yaml_scalar(value: str) -> str:
    return value.strip().strip("'\"").strip()


def _literal_expression(value: str, expected: str) -> bool:
    normalized = _yaml_scalar(value).lower()
    if normalized.startswith("${{") and normalized.endswith("}}"):
        normalized = normalized[3:-2].strip()
    return normalized == expected


def _workflow_run_steps(text: str) -> list[dict[str, str]]:
    """提取真实 ``run`` step，以及 step/job 层的条件和吞错配置。"""

    lines = text.splitlines()
    steps: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)(-\s+)?run:\s*(.*?)\s*$", lines[index])
        if not match:
            index += 1
            continue

        run_index = index
        list_item = match.group(2) is not None
        indentation = len(match.group(1)) + (2 if list_item else 0)
        value = match.group(3)
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            index += 1
            block_lines: list[str] = []
            while index < len(lines):
                line = lines[index]
                if line.strip() and len(line) - len(line.lstrip()) <= indentation:
                    break
                block_lines.append(line.strip())
                index += 1
            command = "\n".join(block_lines)
        else:
            command = _yaml_scalar(value) if value else ""
            index += 1

        step_start = run_index
        step_indent: int | None = len(match.group(1)) if list_item else None
        if not list_item:
            for candidate in range(run_index - 1, -1, -1):
                line = lines[candidate]
                if not line.strip():
                    continue
                candidate_indent = len(line) - len(line.lstrip())
                if candidate_indent < indentation and line.lstrip().startswith("- "):
                    step_start = candidate
                    step_indent = candidate_indent
                    break
                if candidate_indent < indentation and line.lstrip().endswith(":"):
                    break

        step_end = index
        if step_indent is not None:
            step_end = len(lines)
            for candidate in range(index, len(lines)):
                line = lines[candidate]
                if not line.strip():
                    continue
                candidate_indent = len(line) - len(line.lstrip())
                if candidate_indent < step_indent or (
                    candidate_indent == step_indent and line.lstrip().startswith("- ")
                ):
                    step_end = candidate
                    break

        options: dict[str, str] = {"command": command}
        for line in lines[step_start:step_end]:
            content = line.lstrip()
            effective_indentation = len(line) - len(content)
            if content.startswith("- "):
                content = content[2:]
                effective_indentation += 2
            option = (
                re.match(r"^(if|continue-on-error):\s*(.*?)\s*$", content)
                if effective_indentation == indentation
                else None
            )
            if option:
                options[option.group(1)] = option.group(2)

        jobs_index: int | None = None
        jobs_indent = 0
        for candidate in range(run_index - 1, -1, -1):
            jobs_match = re.match(r"^(\s*)jobs:\s*$", lines[candidate])
            if jobs_match:
                jobs_index = candidate
                jobs_indent = len(jobs_match.group(1))
                break
        if jobs_index is not None:
            job_indent: int | None = None
            job_start: int | None = None
            for candidate in range(jobs_index + 1, run_index):
                line = lines[candidate]
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                candidate_indent = len(line) - len(line.lstrip())
                if candidate_indent <= jobs_indent:
                    break
                if job_indent is None:
                    job_indent = candidate_indent
                if candidate_indent == job_indent and re.match(
                    r"^\s*[^:#][^:]*:\s*(?:#.*)?$", line
                ):
                    job_start = candidate
            if job_start is not None and job_indent is not None:
                property_indent = job_indent + 2
                for line in lines[job_start + 1 : run_index]:
                    option = re.match(
                        rf"^\s{{{property_indent}}}(if|continue-on-error):\s*(.*?)\s*$",
                        line,
                    )
                    if option:
                        options[f"job-{option.group(1)}"] = option.group(2)
        steps.append(options)
    return steps


def _run_step_is_blocking(step: dict[str, str]) -> bool:
    if step.get("if") or step.get("job-if"):
        return False
    for key in ("continue-on-error", "job-continue-on-error"):
        value = step.get(key)
        if value and not _literal_expression(value, "false"):
            return False
    return True


def _command_swallows_failure(command: str) -> bool:
    normalized = _normalized(command)
    return bool(
        re.search(r"(?:\|\||;)\s*(?:true|:)(?:\s|$)", normalized)
        or re.search(r"(?:^|[;&])\s*set\s+\+e(?:\s|$)", normalized)
        or re.search(r"(?:^|[;&])\s*exit\s+0(?:\s|$)", normalized)
    )


def _inline_yaml_list(value: str) -> list[str] | None:
    normalized = value.strip()
    if not (normalized.startswith("[") and normalized.endswith("]")):
        return None
    inner = normalized[1:-1].strip()
    if not inner:
        return []
    return [_yaml_scalar(item) for item in inner.split(",") if _yaml_scalar(item)]


def _workflow_pull_request_filters(
    text: str,
) -> tuple[bool, list[str] | None, list[str]]:
    """返回是否监听 PR，以及 paths / paths-ignore。"""

    lines = text.splitlines()
    on_index: int | None = None
    on_indent = 0
    on_value = ""
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)on:\s*(.*?)\s*$", line)
        if match:
            on_index = index
            on_indent = len(match.group(1))
            on_value = match.group(2)
            break
    if on_index is None:
        return False, None, []
    if on_value:
        inline_events = _inline_yaml_list(on_value)
        if inline_events is not None:
            return "pull_request" in inline_events, None, []
        return _yaml_scalar(on_value) == "pull_request", None, []

    block_end = len(lines)
    for index in range(on_index + 1, len(lines)):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip()) <= on_indent:
            block_end = index
            break

    event_index: int | None = None
    event_indent: int | None = None
    event_value = ""
    for index in range(on_index + 1, block_end):
        match = re.match(r"^(\s*)pull_request:\s*(.*?)\s*$", lines[index])
        if match:
            event_index = index
            event_indent = len(match.group(1))
            event_value = match.group(2)
            break
    if event_index is None or event_indent is None:
        return False, None, []
    if event_value and _yaml_scalar(event_value) not in {"", "{}", "null", "~"}:
        return True, [], []

    event_end = block_end
    for index in range(event_index + 1, block_end):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip()) <= event_indent:
            event_end = index
            break

    def read_filter(name: str) -> tuple[bool, list[str]]:
        for filter_index in range(event_index + 1, event_end):
            match = re.match(
                rf"^(\s*){re.escape(name)}:\s*(.*?)\s*$", lines[filter_index]
            )
            if not match or len(match.group(1)) <= event_indent:
                continue
            value = match.group(2)
            inline = _inline_yaml_list(value)
            if inline is not None:
                return True, inline
            if value:
                return True, [_yaml_scalar(value)]

            filter_indent = len(match.group(1))
            patterns: list[str] = []
            for item_index in range(filter_index + 1, event_end):
                line = lines[item_index]
                if not line.strip():
                    continue
                item_indent = len(line) - len(line.lstrip())
                if item_indent <= filter_indent:
                    break
                item = re.match(r"^\s*-\s*(.*?)\s*$", line)
                if item and _yaml_scalar(item.group(1)):
                    patterns.append(_yaml_scalar(item.group(1)))
            return True, patterns
        return False, []

    has_paths, paths = read_filter("paths")
    _, paths_ignore = read_filter("paths-ignore")
    return True, paths if has_paths else None, paths_ignore


def _workflow_has_trigger(text: str, trigger: str) -> bool:
    """检查 workflow 的 ``on`` 顶层是否声明指定事件。"""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)on:\s*(.*?)\s*$", line)
        if not match:
            continue
        indentation = len(match.group(1))
        inline = _inline_yaml_list(match.group(2))
        if inline is not None:
            return trigger in inline
        if match.group(2):
            return _yaml_scalar(match.group(2)) == trigger
        for nested in lines[index + 1 :]:
            if nested.strip() and len(nested) - len(nested.lstrip()) <= indentation:
                break
            event = re.match(r"^\s*([a-zA-Z0-9_-]+):", nested)
            if event and event.group(1) == trigger:
                return True
        return False
    return False


def _validate_workflows(root: Path, errors: list[str]) -> list[dict[str, Any]]:
    """检查稳定入口和 PR 汇总的实际接线。"""
    projection: list[dict[str, Any]] = []
    names = set(CI_WORKFLOWS.values()) | {"real-provider-probe.yml"}
    for name in sorted(names):
        path = f".github/workflows/{name}"
        workflow = _require_repository_path(
            root, path, "workflow", errors, file_only=True
        )
        if workflow is None:
            continue
        text = workflow.read_text(encoding="utf-8")
        trigger = (
            "workflow_dispatch"
            if name == "real-provider-probe.yml"
            else "workflow_call"
        )
        if not _workflow_has_trigger(text, trigger):
            errors.append(f"workflow 不监听 {trigger}：{path}")
        steps = _workflow_run_steps(text)
        for entry in WORKFLOW_ENTRIES.get(name, ()):
            matching = [step for step in steps if entry in _normalized(step["command"])]
            if not matching:
                errors.append(f"workflow 缺少实际 run step：{path} -> {entry}")
            elif not any(
                _run_step_is_blocking(step)
                and not _command_swallows_failure(step["command"])
                for step in matching
            ):
                errors.append(f"workflow 入口被跳过或吞错：{path} -> {entry}")
        projection.append(
            {
                "path": path,
                "trigger": trigger,
                "commands": [step["command"] for step in steps],
            }
        )

    path = ".github/workflows/ci.yml"
    caller = _require_repository_path(root, path, "workflow", errors, file_only=True)
    if caller is None:
        return projection
    text = caller.read_text(encoding="utf-8")
    has_pr, paths, paths_ignore = _workflow_pull_request_filters(text)
    if not has_pr or paths is not None or paths_ignore:
        errors.append("CI caller 必须监听无路径过滤的 pull_request")
    if _workflow_has_trigger(text, "pull_request_target") or re.search(
        r"(?m)^\s*secrets:\s*inherit\s*$", text
    ):
        errors.append("CI caller 不得使用 pull_request_target 或继承 secrets")
    jobs_match = re.search(r"(?ms)^jobs:\s*\n(.*?)(?=^\S|\Z)", text)
    jobs_text = jobs_match.group(1) if jobs_match else ""
    jobs = {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"(?ms)^  ([\w-]+):\s*\n(.*?)(?=^  [\w-]+:\s*(?:#.*)?$|\Z)", jobs_text
        )
    }
    for job, workflow in CI_WORKFLOWS.items():
        block = jobs.get(job, "")
        if not re.search(
            rf"(?m)^    uses:\s*\./\.github/workflows/{re.escape(workflow)}\s*$", block
        ):
            errors.append(f"CI caller 缺少 workflow 调用：{job}")
        conditions = re.findall(r"(?m)^    if:\s*(.*?)\s*$", block)
        expected = f"fromJSON(needs.changes.outputs.selected).{job}"
        if (job == "trust" and conditions) or (
            job != "trust"
            and (
                len(conditions) != 1
                or _yaml_scalar(conditions[0])
                .removeprefix("${{")
                .removesuffix("}}")
                .strip()
                != expected
            )
        ):
            errors.append(f"CI caller 条件错误：{job}")
        if job != "trust" and not re.search(
            r"(?m)^    needs:\s*(?:changes|\[changes\])\s*$", block
        ):
            errors.append(f"CI caller 缺少选择依赖：{job}")
        if re.search(r"(?m)^    continue-on-error:\s*(?!false\s*$)", block):
            errors.append(f"CI caller 不得吞错：{job}")
    required = jobs.get("required", "")
    if not re.search(
        r"(?m)^    name:\s*['\"]?CI required['\"]?\s*$", required
    ) or not re.search(
        r"(?m)^    if:\s*(?:\$\{\{\s*)?always\(\)(?:\s*\}\})?\s*$", required
    ):
        errors.append("CI required 必须始终运行")
    needs = re.search(r"(?m)^    needs:\s*\[([^]]*)\]", required)
    if not needs or {_yaml_scalar(item) for item in needs.group(1).split(",")} != set(
        CI_WORKFLOWS
    ) | {"changes"}:
        errors.append("CI required 必须依赖所有检查")
    for job, entry in (
        ("required", "scripts/ci/gate.py check"),
        ("changes", "scripts/ci/gate.py select"),
    ):
        matching = [
            step
            for step in _workflow_run_steps(
                "jobs:\n  " + job + ":\n" + jobs.get(job, "")
            )
            if re.search(re.escape(entry) + r"(?=\s|$)", step["command"])
        ]
        if job == "required":
            for step in matching:
                step.pop("job-if", None)  # 已单独验证 always()。
        if not any(
            _run_step_is_blocking(step)
            and not _command_swallows_failure(step["command"])
            for step in matching
        ):
            errors.append(f"CI caller 缺少有效入口：{job}")
    if not re.search(
        r"(?m)^\s*NEEDS_JSON:\s*\$\{\{\s*toJSON\(needs\)\s*\}\}\s*$", required
    ):
        errors.append("CI required 缺少结果输入")
    projection.append(
        {
            "path": path,
            "trigger": "pull_request",
            "pull_request_paths": paths,
            "commands": [],
        }
    )
    return projection


def _strip_blockquote_prefixes(
    line: str, max_depth: int | None = None
) -> tuple[str, int]:
    """移除 Markdown 引用容器前缀并返回层级。"""

    depth = 0
    while max_depth is None or depth < max_depth:
        match = MARKDOWN_BLOCKQUOTE_PREFIX.match(line)
        if match is None:
            break
        line = line[match.end() :]
        depth += 1
    return line, depth


def _list_item_content_indent(item_match: re.Match[str]) -> int:
    """返回 list item 正文起点相对于原始行的列数。"""

    return (
        len(item_match.group("indent"))
        + len(item_match.group("marker"))
        + len(item_match.group("spacing"))
    )


def _markdown_body_line(
    line: str, active_list_indent: int | None
) -> tuple[str, int, int | None]:
    """解析引用和单层列表容器中的正文起点。"""

    body, quote_depth = _strip_blockquote_prefixes(line)
    if active_list_indent is not None:
        if not body.strip():
            return "", quote_depth, active_list_indent
        leading_spaces = len(body) - len(body.lstrip(" "))
        if leading_spaces >= active_list_indent:
            stripped = body[active_list_indent:]
            nested_item = MARKDOWN_LIST_ITEM.match(stripped)
            if nested_item:
                content_indent = active_list_indent + _list_item_content_indent(
                    nested_item
                )
                return nested_item.group("content"), quote_depth, content_indent
            return stripped, quote_depth, active_list_indent
        active_list_indent = None

    item = MARKDOWN_LIST_ITEM.match(body)
    if item:
        content_indent = _list_item_content_indent(item)
        return item.group("content"), quote_depth, content_indent
    return body, quote_depth, active_list_indent


def _fence_container_line(
    line: str, quote_depth: int, list_indent: int | None
) -> str | None:
    """返回与 opening fence 相同容器中的候选 closing 行。"""

    body, current_quote_depth = _strip_blockquote_prefixes(line, quote_depth)
    if current_quote_depth < quote_depth:
        return None
    if list_indent is None:
        return body
    leading_spaces = len(body) - len(body.lstrip(" "))
    if leading_spaces < list_indent:
        return None
    return body[list_indent:]


def _visible_markdown_numbered_lines(text: str) -> list[tuple[int, str]]:
    """返回 fenced code block 之外的 Markdown 行及原始行号。"""

    visible: list[tuple[int, str]] = []
    fence_char: str | None = None
    fence_length = 0
    fence_quote_depth = 0
    fence_list_indent: int | None = None
    active_list_indent: int | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if fence_char is not None:
            candidate = _fence_container_line(
                line, fence_quote_depth, fence_list_indent
            )
            if candidate is None and line.strip():
                fence_char = None
                fence_length = 0
                fence_quote_depth = 0
                fence_list_indent = None
            else:
                closing = candidate is not None and re.fullmatch(
                    rf" {{0,3}}{re.escape(fence_char)}{{{fence_length},}}[ \t]*",
                    candidate,
                )
                if closing:
                    fence_char = None
                    fence_length = 0
                    fence_quote_depth = 0
                    fence_list_indent = None
                continue

        body, quote_depth, active_list_indent = _markdown_body_line(
            line, active_list_indent
        )
        opening = MARKDOWN_FENCE_OPEN.match(body)
        if opening:
            fence = opening.group("fence")
            info = opening.group("info")
            if fence.startswith("~") or "`" not in info:
                fence_char = fence[0]
                fence_length = len(fence)
                fence_quote_depth = quote_depth
                fence_list_indent = active_list_indent
                continue
        visible.append((line_number, line))
    return visible


def _visible_markdown_lines(text: str) -> list[str]:
    return [line for _, line in _visible_markdown_numbered_lines(text)]


def _metadata(lines: list[str], label: str) -> str | None:
    for line in lines:
        if line.startswith(label):
            return line.removeprefix(label).strip()
    return None


def _decision_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line.strip()
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return sections


def _evidence_rows(lines: list[str]) -> list[list[str]]:
    """提取 proposed 验收矩阵的数据行。"""

    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _normalized(line) == PROPOSED_EVIDENCE_HEADER
        ),
        None,
    )
    if header_index is None:
        return []

    rows: list[list[str]] = []
    for line in lines[header_index + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        rows.append(
            [cell.strip().strip("`") for cell in stripped.strip("|").split("|")]
        )
    return rows


def _validate_decisions(root: Path, errors: list[str]) -> list[dict[str, str]]:
    decisions_root = root / DECISIONS_PATH
    projection: list[dict[str, str]] = []
    if not decisions_root.is_dir():
        errors.append(f"缺少决策记录目录：{DECISIONS_PATH}")
        return projection

    for lifecycle, headings in DECISION_REQUIRED_HEADINGS.items():
        lifecycle_dir = decisions_root / lifecycle
        if not lifecycle_dir.is_dir():
            errors.append(f"缺少决策 lifecycle 目录：{lifecycle_dir.relative_to(root)}")
            continue
        for path in sorted(lifecycle_dir.glob("*.md")):
            relative = path.relative_to(root)
            if not DECISION_FILE_PATTERN.fullmatch(path.name):
                errors.append(f"决策记录文件名必须是 YYYY-MM-DD-topic.md：{relative}")
            lines = _visible_markdown_lines(path.read_text(encoding="utf-8"))
            sections = _decision_sections(lines)
            status = _metadata(lines, "状态：")
            if status != lifecycle:
                errors.append(f"{relative} 状态必须是 {lifecycle}，实际为 {status!r}")
            decision_type = _metadata(lines, "类型：")
            if decision_type not in DECISION_TYPES:
                errors.append(
                    f"{relative} 类型必须是 {sorted(DECISION_TYPES)} 之一，实际为 {decision_type!r}"
                )
            owner = _metadata(lines, "Owner：")
            _require_repository_path(
                root, owner, f"{relative} Owner", errors, file_only=True
            )
            for heading in headings:
                if heading not in sections:
                    errors.append(f"{relative} 缺少标题：{heading}")
                elif not any(
                    line.strip() and not line.lstrip().startswith("<!--")
                    for line in sections[heading]
                ):
                    errors.append(f"{relative} 标题下没有内容：{heading}")
            if lifecycle in {"implemented", "archived"}:
                for heading in IMPLEMENTED_BANNED_HEADINGS:
                    if heading in sections:
                        errors.append(
                            f"当前/归档记录不能保留提案或进度标题：{relative} -> {heading}"
                        )
            if lifecycle == "proposed" and "## 验收标准" in sections:
                acceptance_lines = sections["## 验收标准"]
                has_header = any(
                    _normalized(line) == PROPOSED_EVIDENCE_HEADER
                    for line in acceptance_lines
                )
                if not has_header:
                    errors.append(f"{relative} proposed 验收标准缺少证据矩阵表头")
                else:
                    rows = _evidence_rows(acceptance_lines)
                    if not rows:
                        errors.append(f"{relative} proposed 验收标准缺少证据矩阵数据行")
                    for row in rows:
                        if len(row) != 6 or not all(row):
                            errors.append(
                                f"{relative} proposed 证据矩阵必须填写全部六列"
                            )
                            continue
                        if row[-1] not in EVIDENCE_RESULTS:
                            errors.append(
                                f"{relative} proposed 证据结果必须是 {sorted(EVIDENCE_RESULTS)} 之一，实际为 {row[-1]!r}"
                            )
            if decision_type == "simplification" and lifecycle in {
                "proposed",
                "implemented",
            }:
                target_heading = "## 验收标准" if lifecycle == "proposed" else "## 验证"
                target_text = "\n".join(sections.get(target_heading, []))
                for label in SIMPLIFICATION_REQUIRED_LABELS:
                    if label not in target_text:
                        errors.append(
                            f"{relative} simplification {target_heading} 缺少：{label}"
                        )
            projection.append(
                {
                    "path": relative.as_posix(),
                    "status": status or "missing",
                    "type": decision_type or "missing",
                    "owner": owner or "missing",
                }
            )
    if not projection:
        errors.append("至少需要一份 tracked decision record")
    return projection


def _validate_postmortems(root: Path, errors: list[str]) -> list[str]:
    """检查复盘入口与模板结构，语义充分性仍由 Reviewer 裁决。"""

    checked: list[str] = []
    readme = root / POSTMORTEMS_PATH / "README.md"
    template = root / POSTMORTEMS_PATH / "TEMPLATE.md"
    for path in (readme, template):
        if not path.is_file():
            errors.append(f"缺少 postmortem 入口或模板：{path.relative_to(root)}")
            continue
        checked.append(path.relative_to(root).as_posix())

    if template.is_file():
        sections = _decision_sections(
            _visible_markdown_lines(template.read_text(encoding="utf-8"))
        )
        for heading in POSTMORTEM_TEMPLATE_HEADINGS:
            if heading not in sections:
                errors.append(f"postmortem 模板缺少标题：{heading}")
            elif not any(line.strip() for line in sections[heading]):
                errors.append(f"postmortem 模板标题下没有内容：{heading}")
    return checked


def _attribute_root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _validate_agents_files(root: Path, errors: list[str]) -> list[dict[str, Any]]:
    """检查分层 AGENTS 指令的链接、单一 H1 与字符预算。"""

    projection: list[dict[str, Any]] = []
    for relative, budget in AGENTS_FILE_BUDGETS.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"缺少 AGENTS 指令文件：{relative}")
            continue
        text = path.read_text(encoding="utf-8")
        visible = _visible_markdown_lines(text)
        if sum(1 for line in visible if line.startswith("# ")) != 1:
            errors.append(f"AGENTS 指令必须有且只有一个 H1：{relative}")
        for line in visible:
            for link in AGENTS_LINK_PATTERN.findall(line):
                target = link.split("#", 1)[0]
                if not target or target.startswith(EXTERNAL_LINK_PREFIXES):
                    continue
                if not (path.parent / target).resolve().exists():
                    errors.append(f"AGENTS 指令引用失效：{relative} -> {link}")
        if len(text) > budget:
            errors.append(f"AGENTS 指令超出字符预算：{relative} {len(text)} > {budget}")
        projection.append({"path": relative, "chars": len(text), "budget": budget})
    return projection


def _is_formal_docs_markdown(path: Path) -> bool:
    """`docs/vibe/` 与任何 `node_modules` 都是本仓库非正式资料。"""

    parts = set(path.parts)
    return not (parts & {"vibe", "node_modules"})


def _validate_document_prose(root: Path, errors: list[str]) -> int:
    """拒绝正式文档中的对举式否定，fenced code block 不参与检查。"""

    docs_root = root / "docs"
    checked = 0
    for path in sorted(docs_root.rglob("*.md")):
        if not _is_formal_docs_markdown(path):
            continue
        checked += 1
        relative = path.relative_to(root)
        for line_number, line in _visible_markdown_numbered_lines(
            path.read_text(encoding="utf-8")
        ):
            if DOCUMENT_PROSE_CONTRAST.search(line):
                errors.append(
                    f"文档应直接陈述目标事实，禁止对举式否定：{relative}:{line_number}"
                )
    return checked


def _validate_router_boundaries(root: Path, errors: list[str]) -> int:
    routers_root = root / "backend/yuxi/api/routers"
    checked = 0
    for path in sorted(routers_root.rglob("*.py")):
        checked += 1
        relative = path.relative_to(root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except SyntaxError as exc:
            errors.append(f"router 无法解析：{relative}:{exc.lineno} {exc.msg}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if not any(
                    module == "sqlalchemy" or module.startswith("sqlalchemy.")
                    for module in modules
                ):
                    continue
                allowed_async_session = (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "sqlalchemy.ext.asyncio"
                    and {alias.name for alias in node.names} <= {"AsyncSession"}
                )
                if not allowed_async_session:
                    errors.append(
                        f"router 不得拥有 SQLAlchemy query builder：{relative}:{node.lineno}"
                    )
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            receiver = _attribute_root_name(node.func.value)
            if receiver in ROUTER_DB_RECEIVERS and node.func.attr in ROUTER_DB_METHODS:
                errors.append(
                    f"router 不得直接执行持久化操作：{relative}:{node.lineno} -> {receiver}.{node.func.attr}"
                )
    return checked


def _validate_frontend_api_boundary(root: Path, errors: list[str]) -> int:
    source_root = root / "frontend/src"
    checked = 0
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".js", ".ts", ".vue"}:
            continue
        if (source_root / "apis") in path.parents:
            continue
        checked += 1
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if DIRECT_FRONTEND_API_LITERAL.search(line):
                errors.append(
                    f"frontend/src/apis 外不得拥有 /api 路径：{path.relative_to(root)}:{line_number}"
                )
    return checked


def _validate_workspace_host_path_boundary(root: Path, errors: list[str]) -> int:
    """普通 use-case 与 repository 不得取得 UserWorkspace 宿主路径。"""

    checked = 0
    for source_root in (
        sorted((root / "backend/yuxi/modules").glob("*/services"))
        + sorted((root / "backend/yuxi/modules").glob("*/repositories"))
        + [root / "backend/yuxi/modules/extensions/skills"]
    ):
        for path in sorted(source_root.rglob("*.py")):
            checked += 1
            relative = path.relative_to(root)
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(relative))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                forbidden: set[str] = set()
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "yuxi.modules.workspace.paths"
                ):
                    forbidden.update(
                        WORKSPACE_HOST_PATH_EXPORTS.intersection(
                            alias.name for alias in node.names
                        )
                    )
                    if relative == PERSONAL_SKILL_WORKSPACE_OWNER:
                        forbidden.discard("user_workspace_dir")
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "yuxi.infrastructure.runtime_settings"
                ):
                    if any(alias.name == "get_user_data_dir" for alias in node.names):
                        forbidden.add("get_user_data_dir")
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "yuxi.modules.workspace"
                ):
                    if any(alias.name == "paths" for alias in node.names):
                        forbidden.add("paths module")
                elif isinstance(node, ast.Import):
                    imported = {alias.name for alias in node.names}
                    if "yuxi.modules.workspace.paths" in imported:
                        forbidden.add("paths module")
                    if "yuxi.infrastructure.runtime_settings" in imported:
                        forbidden.add("runtime settings module")
                if forbidden:
                    errors.append(
                        "普通 Service/Repository 不得取得 UserWorkspace 宿主 Path："
                        f"{relative}:{node.lineno} -> {', '.join(sorted(forbidden))}"
                    )
            if "YUXI_USER_DATA_DIR" in source:
                errors.append(
                    "普通 Service/Repository 不得读取 UserWorkspace 宿主根环境变量："
                    f"{relative}"
                )
    return checked


def verify(root: Path) -> tuple[list[str], dict[str, Any]]:
    """验证 Owner-local 契约，并返回按需派生的审计投影。"""

    resolved_root = root.resolve()
    errors: list[str] = []
    for forbidden in FORBIDDEN_CENTRAL_INVENTORIES:
        if (resolved_root / forbidden).exists():
            errors.append(
                f"禁止手工中央主张清单；主张必须在语义 Owner 处闭合：{forbidden}"
            )
    decisions = _validate_decisions(resolved_root, errors)
    postmortems = _validate_postmortems(resolved_root, errors)
    workflows = _validate_workflows(resolved_root, errors)
    agents_files = _validate_agents_files(resolved_root, errors)
    document_files = _validate_document_prose(resolved_root, errors)
    router_files = _validate_router_boundaries(resolved_root, errors)
    frontend_files = _validate_frontend_api_boundary(resolved_root, errors)
    workspace_boundary_files = _validate_workspace_host_path_boundary(
        resolved_root, errors
    )
    projection = {
        "derived": True,
        "authority": "owner-local code, tests, decisions and workflows",
        "decisions": decisions,
        "postmortems": postmortems,
        "workflows": workflows,
        "agents_files": agents_files,
        "boundaries": {
            "document_files_checked": document_files,
            "router_files_checked": router_files,
            "frontend_source_files_checked": frontend_files,
            "workspace_boundary_files_checked": workspace_boundary_files,
        },
    }
    return errors, projection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        action="store_true",
        help="打印从当前 Owner 派生的临时 JSON 审计视图",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    errors, projection = verify(root)
    if args.report:
        print(json.dumps(projection, ensure_ascii=False, indent=2))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"工程信任检查失败：{len(errors)} 个问题", file=sys.stderr)
        return 1
    if not args.report:
        print(
            "工程信任检查通过："
            f"{len(projection['decisions'])} decisions / "
            f"{len(projection['workflows'])} workflows / "
            f"{len(projection['agents_files'])} agents files / "
            f"{projection['boundaries']['document_files_checked']} docs / "
            f"{projection['boundaries']['router_files_checked']} routers / "
            f"{projection['boundaries']['frontend_source_files_checked']} frontend sources"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
