# 移除个人 Skill 专用安装工具，使用沙盒标准 CLI

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/extensions/tools/builtin/__init__.py

## 问题

`install_skill` 只安装当前用户的个人 Skill，其来源下载和文件复制流程与沙盒命令执行能力重复。用户需要从 Agent 使用标准 CLI 安装，也继续需要网页中的个人安装入口。

## 决策

### 实现方案

删除 `install_skill` 工具、注册、导出与仅供该工具调用的来源安装和沙盒下载函数。Agent 通过已有 `execute` 执行 `npx -y skills add <source> -g -y`；沙盒的 `~/.agents/skills` 链接到 `/home/user/workspace/agents/skills`，直接写入原有个人持久目录。运行时仍由个人 Skill 扫描器发现文件。

网页个人与共享安装保持现有入口、权限、草稿确认和发布服务，不修改前端。个人安装 API 继续复用 `personal.py` 的包校验、创建型原子发布与同名拒绝；个人列表、读取、删除、同名覆盖及运行时发现继续有效。草稿获取和远程下载仍有网页消费者，保留这些能力。

Cube 模板建立标准链接；已有 VM 依运维说明补建，不覆盖已有目录。Container 镜像提供 Node/npm；runner 单独挂载经过 no-follow 校验的当前用户个人目录，在 HOME tmpfs 挂载完成后建立链接，成功后才接受用户命令。HOME tmpfs 允许执行 npm 缓存中的 CLI，`/tmp` 保持 noexec。挂载或链接建立失败明确拒绝创建并清理容器。

本记录仅取代[安装草稿契约](./2026-09-29-skill-draft-contract.md)中的 Agent 专用直接安装流程。网页安装与来源发布规则继续由原服务拥有。不增加安装包装器、目录同步服务、路径配置或数据迁移。

## 替代方案

- 保留专用工具：继续维护与沙盒能力重复的来源下载和 ToolMessage 装配。
- 只删工具注册：留下没有消费者的工具实现和来源下载服务。
- 同时删除网页个人安装：改变已有产品能力，超出工具删除范围。
- 删除工具及其专用流程，复用标准 CLI：满足 Agent 安装需求并保留网页入口。

## 后果

网页个人安装维持同名拒绝与原子发布；沙盒 CLI 的覆盖、更新和锁文件遵循 CLI 自身语义。两种入口写入同一用户目录，受原有文件读取、发现与用户隔离边界约束。管理页来源白名单继续约束网页安装，不约束用户沙盒命令的网络访问。

Container 联网安装需要允许网络；HOME 随 Run 销毁，个人 Skill 文件跨 Run 保留。既有 Cube VM 不随模板更新，需按[沙盒运维](../../../advanced/sandbox-operations.md)显式补建链接，不自动删除或迁移 Home 文件。

## 验证

- 真实 Docker integration：完整沙盒 28 项通过；空 Workdir、普通项目及个人 Skill 目录自身的跨 Run 回读 3 项通过；不指定 Agent 的默认全局 CLI 安装与删除 1 项通过，均独立回读宿主文件。no-follow 拒绝符号链接路径，链接初始化失败回收容器。
- 原有个人安装、导入命名、共享编辑与资源授权 HTTP integration：19 passed，包含普通用户上传到 personal 后的文件读取与删除。真实浏览器上传、选择个人、确认、文件字节回读与删除通过，个人/共享选择保留。
- `test/e2e/test_personal_skill_agent_e2e.py`：2 passed，同时覆盖网页安装，以及前一个真实 Run 经标准入口写入，后续 Run 自动发现并读取；移除发现路径会被独立 oracle 拒绝。
- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`：3493 passed、125 skipped。工程契约检查及 56 项验证器测试、Ruff、文档链接检查与构建、`git diff --check` 通过。前端无代码改动。
- Cube 真实 VM integration 为 Not run：未配置本次变更对应的新模板环境。

旧能力不存在：工具注册与产品代码不再包含 `install_skill`、`install_personal_skills_from_source` 或 `_download_sandbox_skill`；网页个人安装接口及其测试继续保留。

重新引入条件：出现必须由独立 Agent 工具拥有、无法通过沙盒标准命令或已有网页安装完成的真实需求，并单独决策。
