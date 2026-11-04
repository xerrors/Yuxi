# 后端依赖使用官方 PyPI

状态：implemented
类型：bug-fix
Owner：backend/pyproject.toml

## 问题

后端单测 CI 在安装锁定依赖时，从清华镜像下载 `pypinyin` 收到 HTTP 403。项目默认索引和全部第三方锁定下载地址都绑定该镜像，单个包的替换或 CI 重试不能移除共同故障入口。

## 决策

### 实现方案

移除项目的清华默认索引设置，使用 uv 默认的官方 PyPI。锁文件的 registry 改为 `https://pypi.org/simple`，发行文件地址改为官方文件域名；包版本、文件路径、哈希、依赖关系和平台条件全部保持不变。CI 与 Docker 继续消费同一锁文件，不增加重试或备用源。Docker 注释说明下载地址由锁文件拥有。

## 替代方案

- 只重跑 CI：没有修复镜像拒绝下载的原因。
- 只覆盖 CI 索引：锁内绝对下载地址仍指向镜像。
- 只替换失败包：其他发行文件仍保留同一故障入口。

## 后果

CI、Docker 与开发环境共用官方来源的锁文件，构建需要访问官方 PyPI 及其文件域名。锁文件差异只涉及 202 处 registry 引用和 1558 个发行文件 URL；197 个包的版本、文件哈希、路径和平台条件保持不变。

## 验证

- Passed：来源配置的两项 unit。在临时目录恢复旧默认索引或 registry 时分别失败；只恢复一条发行文件镜像 URL、registry 保持官方值时，测试因该 URL 失败。
- Passed：锁文件字节级与 TOML 结构对比，仅允许两种 URL 前缀变化；从官方地址实际下载 `pypinyin` wheel，文件大小与 SHA-256 均匹配锁定值。
- Passed：`UV_DEFAULT_INDEX=https://pypi.org/simple uv lock --project backend --check --offline`。本地显式设置该变量以隔离用户级镜像配置；项目默认值由新来源测试独立验证。
- Passed：CI 同版 uv 0.12.6、Python 3.13、隔离的跟踪源码副本、临时 venv 和空缓存下执行 `uv sync --locked --group test`，安装 193 个包；`uv pip check --python <临时venv>/bin/python` 检查全部兼容；实际调用 `pypinyin.lazy_pinyin("语析")` 返回 `["yu", "xi"]`。
- Passed：`docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`，3605 通过、127 跳过；容器缺少仓库根目录的配置测试由 `uv run --project backend --no-sync --group test pytest backend/test/unit/config -q -p no:cacheprovider` 补验，209 通过。
- Passed：工程契约检查及其 56 项单测、来源测试的 Ruff 检查、文档构建和 `git diff --check`。

冷安装验证当前 Linux 平台；其他平台的发行文件来源通过相同路径和哈希迁移核对。
