# 沙盒原生文件搜索适配

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/backends/sandbox/backend.py

## 问题

DeepAgents 的 grep 结果以 NUL 分隔文件名与行号，sandbox shell 文本通道丢失 NUL 后导致命中结果解析失败。自建 Python 搜索脚本会重复承担遍历、glob、文件打开和匹配职责。Sandbox 1.11.0 已提供结构化文件搜索 API。

## 决策

Yuxi 直接调用 `/v1/file/grep`，使用 `fixed_strings=true`，把原生路径、行号、文本和截断标志映射为 `GrepResult`。请求使用现有 sandbox connection 与 provisioner 凭据；搜索根和跨根全局 `max_count` 由 backend 拥有。同步和异步入口使用同一搜索流程，HTTP 请求与异步等待均有超时。目录 glob 转为以当前搜索根锚定的完整路径过滤，路径前缀中的 glob 元字符按原生规则转义。

agent-sandbox 0.0.30 把 HTTP 200 下的 `success=false` 错误也解析为成功模型，缺失目录因此触发 `data.pattern` 校验异常。HTTP 适配先检查状态与 success，只有成功数据交给 SDK 的 `FileGrepResult` 校验。`not_found` 返回空结果，其他失败明确返回错误；不解析异常字符串判断缺失目录。

用户明确接受原生搜索的容器内符号链接行为。请求路径仍必须位于可读根，glob 拒绝 `..`；显式根内链接可以读取容器内目标，结果路径过滤不被视为 no-follow 授权。跨用户隔离仍由 provisioner 的 uid、挂载与独立容器执行。宿主 Workspace 的 no-follow 契约保持独立。

## 替代方案

保留或收窄内嵌搜索脚本仍需维护遍历与文件读取；Base64 包装保留了 shell 传输及第三方命令模板耦合；严格 no-follow 搜索需要执行端能力与镜像交付改造。原生 API 薄适配删除重复搜索实现，接受原生语义。移除 grep 会破坏现有模型文件工具 consumer。

## 后果

原生服务默认不搜索隐藏文件，可通过显式 glob 选择。未指定 `max_count` 时采用 1.11.0 每根 500 条的原生默认限额并透传截断标志；显式限额按剩余额度传给各根。结果顺序由服务拥有。单根匹配序列化后的 UTF-8 大小超过 `SANDBOX_MAX_OUTPUT_BYTES` 时返回错误，该限制在收到响应后检查，不是远端内存限制。异步等待或 HTTP 超时不承诺终止远端搜索进程。

## 验证

旧能力不存在：`_GREP_SCRIPT`、`shlex` 和 grep 专用 shell `truncate` 参数均已删除。
重新引入条件：原生 API 出现无法在薄适配内解决的已复现缺陷，并重新评估执行端 Owner 与交付成本。

- Passed：`docker run --rm --user 0 --entrypoint python -v "$PWD:/workspace" -w /workspace/backend yuxi-api:0.7.3 -m pytest test/unit -m 'not slow' -q -p no:cacheprovider --disable-warnings --tb=short`，2355 项通过，6 项警告。独立工作树无 Compose 槽位，使用开发镜像加载目标代码执行 unit。
- Passed：临时启动无用户数据挂载、`--network none` 的 sandbox 1.11.0，测试容器使用 `--network container:yuxi-grep-native-probe` 和 `TEST_SANDBOX_URL=http://127.0.0.1:8080`，执行 `python -m pytest test/integration/backends/test_sandbox_native_grep.py --confcutdir=test/integration/backends -q -p no:cacheprovider --tb=short`。回读真实 HTTP 匹配与模型 `ToolMessage`，覆盖字面量、冒号文件名、glob、多根限额、空结果、缺失 Skills 根、隐藏文件、长行、输出上限、特殊目录名及显式链接行为。`--confcutdir` 隔离不相关的全站账户和 provisioner 清理；测试自行清理唯一临时目录。
- Passed：`python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`；后者 62 项通过。
- Passed：`uv tool run ruff check` 与 `uv tool run ruff format --check` 覆盖 backend、unit 与 integration 三个修改文件；`cd docs && pnpm run build` 通过（构建产物体积提示）；`git diff --check HEAD` 通过。
- Passed：独立 Reviewer 审查最终 diff；审查发现的路径前缀 glob 元字符漏匹配已修复，修复后沙盒 unit 93 项与真实 HTTP 集成 1 项均通过。
- Not run：完整 Agent/API/worker assembled-path E2E；目标工作树未启动独立完整 Compose 槽位。真实沙盒 HTTP 与模型工具探针不替代 Run 生命周期验证。
