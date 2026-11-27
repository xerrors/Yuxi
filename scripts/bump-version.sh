#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Yuxi 版本号升级脚本
# =============================================================================
# 用法: ./scripts/bump-version.sh [--dev] <新版本号>
# 示例: ./scripts/bump-version.sh 0.6.2
# 示例: ./scripts/bump-version.sh --dev 0.6.2.dev1
#
# 该脚本从 backend/pyproject.toml 读取当前版本，
# 自动同步所有需要硬编码版本号的位置。
# --dev 模式不会更新 README、快速开始、部署指南和文档首页中的发布版本引用。
#
# 更新 tag 标准流程（给人工和 agent 使用）:
#
# 1. 先判断目标版本，不要直接打 tag。
#    - 用户给完整版本号时，直接使用该版本，例如 0.7.1.dev2。
#    - 用户只说“更新到 dev2”时，沿用当前主版本号 x.y.z，目标版本为 x.y.z.dev2。
#      例如当前是 0.7.1.dev1，则运行: ./scripts/bump-version.sh --dev 0.7.1.dev2
#    - 用户只说“更新 tag”或“添加 tag”时，先读取 backend/pyproject.toml:
#      * 如果当前是 x.y.z.dev0，默认推断为要发布第一个开发 tag，目标版本是 x.y.z.dev1。
#      * 如果当前已经是 x.y.z.devN（N >= 1）或 x.y.z，默认给当前版本打 tag。
#      * 如果上下文里提到 dev2/dev3 等，则按提到的 devN 推断目标版本。
#
# 2. 如需升级版本，先运行本脚本。开发版必须带 --dev:
#    ./scripts/bump-version.sh --dev 0.7.1.dev2
#    正式版不带 --dev:
#    ./scripts/bump-version.sh 0.7.1
#
# 3. 脚本运行后必须检查:
#    - git diff，确认只有预期版本文件变化。
#    - backend/pyproject.toml、frontend/package.json、
#      docker-compose*.yml、backend/uv.lock 中的 Yuxi 版本一致。
#    - dev 模式下 README.md、README.en.md、docs/intro/quick-start.md 和文档首页
#      不应被更新。
#
# 4. 先提交并推送版本更新，等待该提交的 CI 全部通过，再创建 tag。tag 必须指向通过检查的同一提交:
#    git add backend/pyproject.toml backend/uv.lock docker-compose.yml docker-compose.prod.yml frontend/package.json
#    git commit -m 'chore(release): 升级版本到 0.7.1.dev2'
#    git tag v0.7.1.dev2
#
# 5. 创建 tag 后必须检查:
#    - git status --short 应为空。
#    - git show --no-patch --decorate --oneline HEAD 应显示 tag。
#    - git tag --points-at HEAD 应包含刚创建的 tag。
#    - 如果 tag 已存在，不要强制覆盖，先停下来确认。
#
# 6. 推送时推荐显式推送当前分支和本次 tag，避免误推其它本地 tag:
#    git push origin main
#    git push origin v0.7.1.dev2
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 检查参数
DEV_MODE=false
if [ "${1:-}" = "--dev" ]; then
    DEV_MODE=true
    shift
fi

if [ $# -ne 1 ]; then
    echo "用法: $0 [--dev] <新版本号>"
    echo "示例: $0 0.6.2"
    echo "示例: $0 --dev 0.6.2.dev1"
    exit 1
fi

NEW_VERSION="$1"

# 验证版本号格式（支持 x.y.z 或 x.y.z.devN）
if [[ ! "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(\.[a-zA-Z0-9]+)?$ ]]; then
    echo "错误: 版本号格式无效，期望格式如 0.6.2 或 0.6.2.dev1"
    exit 1
fi

# 读取当前版本号（以 backend/pyproject.toml 为 SSOT）
PYPROJECT_FILE="${PROJECT_ROOT}/backend/pyproject.toml"
if [ ! -f "$PYPROJECT_FILE" ]; then
    echo "错误: 找不到 ${PYPROJECT_FILE}"
    exit 1
fi

CURRENT_VERSION=$(grep -E '^version[[:space:]]*=[[:space:]]*"' "$PYPROJECT_FILE" | head -1 | sed -E 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/')

if [ -z "$CURRENT_VERSION" ]; then
    echo "错误: 无法从 ${PYPROJECT_FILE} 读取当前版本号"
    exit 1
fi

if [ "$CURRENT_VERSION" = "$NEW_VERSION" ]; then
    echo "当前版本已经是 ${NEW_VERSION}，无需更新"
    exit 0
fi

echo "准备将版本号从 ${CURRENT_VERSION} 升级到 ${NEW_VERSION}"
echo "受影响的文件:"
echo "  - backend/pyproject.toml"
echo "  - frontend/package.json"
echo "  - docker-compose.yml"
echo "  - docker-compose.prod.yml"
echo "  - backend/test/e2e/web-tools.compose.yml"
echo "  - sandbox runner 与集成测试的默认镜像"
echo "  - backend/uv.lock"
if [ "$DEV_MODE" = false ]; then
    echo "  - README.md"
    echo "  - README.en.md"
    echo "  - docs/intro/quick-start.md"
    echo "  - docs/advanced/deployment.md"
    echo "  - docs/advanced/container-sandbox.md"
    echo "  - docs/.vitepress/theme/components/YuxiHome.vue"
    echo "  - 当前候选更新说明与 changelog 候选入口"
fi
echo ""
read -rp "确认继续? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# -----------------------------------------------------------------------------
# 1. 更新后端版本 (backend/pyproject.toml)
# -----------------------------------------------------------------------------
echo "→ 更新 backend/pyproject.toml"
perl -pi -e "s/^version = \"[^\"]+\"/version = \"${NEW_VERSION}\"/" \
    "${PROJECT_ROOT}/backend/pyproject.toml"

# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# 3. 更新前端版本 (frontend/package.json)
# -----------------------------------------------------------------------------
echo "→ 更新 frontend/package.json"
perl -pi -e "s/\"version\": \"[^\"]+\"/\"version\": \"${NEW_VERSION}\"/" \
    "${PROJECT_ROOT}/frontend/package.json"

# -----------------------------------------------------------------------------
# 4. 更新 Docker Compose 镜像标签默认值
# -----------------------------------------------------------------------------
echo "→ 更新 docker-compose.yml"
perl -pi -e "s/\\\$\\{YUXI_VERSION:-[^}]+\\}/\\\${YUXI_VERSION:-${NEW_VERSION}}/g" \
    "${PROJECT_ROOT}/docker-compose.yml"

echo "→ 更新 docker-compose.prod.yml"
perl -pi -e "s/\\\$\\{YUXI_VERSION:-[^}]+\\}/\\\${YUXI_VERSION:-${NEW_VERSION}}/g" \
    "${PROJECT_ROOT}/docker-compose.prod.yml"

echo "→ 更新网络工具 E2E 镜像版本"
perl -pi -e "s/\\\$\\{YUXI_VERSION:-[^}]+\\}/\\\${YUXI_VERSION:-${NEW_VERSION}}/g" \
    "${PROJECT_ROOT}/backend/test/e2e/web-tools.compose.yml"

echo "→ 更新 sandbox runner 与集成测试的默认镜像"
for sandbox_source in .env.template backend/yuxi/sandbox_runner/settings.py backend/test/integration/sandbox/test_container_sandbox.py; do
    perl -pi -e "s/(yuxi-sandbox:)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
        "${PROJECT_ROOT}/${sandbox_source}"
done

# -----------------------------------------------------------------------------
# 5. 更新 uv.lock 中的项目版本号
# -----------------------------------------------------------------------------
echo "→ 更新 backend/uv.lock"
# yuxi 包版本
LOCK_VERSION=$(printf '%s' "$NEW_VERSION" | sed -E 's/\.alpha([0-9]+)$/a\1/; s/\.beta([0-9]+)$/b\1/; s/\.rc([0-9]+)$/rc\1/')
perl -0pi -e "s/(^name = \"yuxi\"\nversion = \")[^\"]+/\${1}${LOCK_VERSION}/m" \
    "${PROJECT_ROOT}/backend/uv.lock"

# -----------------------------------------------------------------------------
# 6. 更新文档中的版本引用
# -----------------------------------------------------------------------------
# 只有正式发布才移动稳定版安装入口。
if [ "$DEV_MODE" = false ]; then
    if [[ "$NEW_VERSION" != *.*.*.* ]]; then
        echo "→ 更新 README.md"
        perl -pi -e "s/(git clone --branch v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g; s/(当前仓库默认配置对应 \`v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
            "${PROJECT_ROOT}/README.md"

        echo "→ 更新 README.en.md"
        perl -pi -e "s/(git clone --branch v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
            "${PROJECT_ROOT}/README.en.md"

        echo "→ 更新 docs/intro/quick-start.md"
        perl -pi -e "s/(git clone --branch v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g; s/(仓库当前默认配置对应 \`v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
            "${PROJECT_ROOT}/docs/intro/quick-start.md"

        echo "→ 更新 docs/advanced/deployment.md"
        perl -pi -e "s/(升级到当前 \`v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
            "${PROJECT_ROOT}/docs/advanced/deployment.md"

        echo "→ 更新 docs/advanced/container-sandbox.md"
        perl -pi -e "s/(yuxi-sandbox:)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
            "${PROJECT_ROOT}/docs/advanced/container-sandbox.md"

        echo "→ 更新 docs/.vitepress/theme/components/YuxiHome.vue"
        perl -pi -e "s/(git clone --branch v)[0-9]+\\.[0-9]+\\.[0-9]+(\\.[a-zA-Z0-9]+)?/\${1}${NEW_VERSION}/g" \
            "${PROJECT_ROOT}/docs/.vitepress/theme/components/YuxiHome.vue"
    elif [[ "$CURRENT_VERSION" == *.*.*.* ]]; then
        # 精确更新当前候选引用，保留其他发布版本和历史章节。
        current_version_pattern="(?<![0-9.])\\Q${CURRENT_VERSION}\\E(?![0-9A-Za-z])"
        for current_doc in README.md README.en.md docs/intro/quick-start.md docs/advanced/deployment.md \
            docs/advanced/container-sandbox.md docs/.vitepress/theme/components/YuxiHome.vue; do
            perl -pi -e "s/${current_version_pattern}/${NEW_VERSION}/g" "${PROJECT_ROOT}/${current_doc}"
        done
        release_notes="${PROJECT_ROOT}/docs/develop-guides/releases/$(printf '%s' "$NEW_VERSION" | cut -d. -f1-3).md"
        if [ -f "$release_notes" ]; then
            perl -pi -e "s/${current_version_pattern}/${NEW_VERSION}/g" "$release_notes"
        fi
        perl -pi -e "s/${current_version_pattern}/${NEW_VERSION}/g if /当前候选/" \
            "${PROJECT_ROOT}/docs/develop-guides/changelog.md"
    fi
else
    echo "→ dev 模式，跳过 README、快速开始、部署指南和文档首页的发布版本更新"
fi

# -----------------------------------------------------------------------------
# 7. 验证
# -----------------------------------------------------------------------------
echo ""
echo "版本号升级完成，验证结果:"
echo ""

echo "  backend/pyproject.toml:"
grep -E "^version = \"" "${PROJECT_ROOT}/backend/pyproject.toml" | head -1 | sed 's/^/    /'

echo "  frontend/package.json:"
grep -E '"version"' "${PROJECT_ROOT}/frontend/package.json" | head -1 | sed 's/^/    /'

echo "  docker-compose.yml (api):"
grep -E "image: .*api:.*YUXI_VERSION" "${PROJECT_ROOT}/docker-compose.yml" | head -1 | sed 's/^/    /'

echo "  docker-compose.prod.yml (frontend):"
grep -E "image: .*frontend:.*YUXI_VERSION" "${PROJECT_ROOT}/docker-compose.prod.yml" | head -1 | sed 's/^/    /'

echo ""
echo "后续步骤:"
echo "  1. 检查 git diff 确认修改无误"
echo "  2. 仅提交本次版本改动并推送当前分支"
echo "  3. 候选验收通过后，按贡献指南创建并显式推送 v${NEW_VERSION} tag"
