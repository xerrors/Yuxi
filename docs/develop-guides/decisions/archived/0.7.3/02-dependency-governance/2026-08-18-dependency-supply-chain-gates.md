# 依赖供应链审计门禁

状态：archived
类型：process
Owner：.github/workflows/dependency-audit.yml

## 问题

Python 与 Node.js 的锁定依赖没有漏洞和许可证审计 gate。新增或更新直接依赖时，传递闭包中的已知漏洞、强 copyleft 或未知许可证可以在合并前保持不可见。

## 决策

依赖审计 workflow 以 shipping 锁文件为事实来源，并只在 manifest、锁文件、审计 workflow、Makefile 或固定脆弱 fixture 变化时自动运行；同一分支的新运行取消已经过期的审计。Python 漏洞直接运行 `uv audit`，Node.js 漏洞直接运行 `pnpm audit`；backend 受 PyTorch 版本约束的 advisory 使用工具原生 `--ignore` 明确列出。固定脆弱 fixture 由同一 workflow 执行，证明 Python 与 Node.js 审计会因已知漏洞返回失败。Python 许可证通过隔离生产环境运行 `pip-licenses`，输出 backend 与 yuxi-cli 的传递依赖报告供 Review 使用，不自动判断法律兼容性。

Dependabot 的常规版本策略由[依赖更新降噪策略](./2026-08-19-dependency-update-policy.md)拥有；本记录只拥有漏洞与许可证审计 gate。

容器镜像扫描不属于当前阻断 gate。生产镜像由多个本地 Dockerfile、Compose 基础镜像和外部 sandbox 镜像组成；加入镜像扫描前需要确定构建产物、扫描时点和基础镜像例外 Owner。

## 替代方案

- 立即要求所有锁文件零漏洞：backend 当前受 PyTorch/torchvision 版本约束，会让新增 gate 从第一天起不可用。
- 让审计步骤 `continue-on-error` 或使用 `|| true`：只产生日志，不形成合并拒绝后果。
- 只扫描直接依赖声明：无法覆盖本问题关注的传递依赖。
- 自动维护包、版本与许可证允许清单：复制锁文件和包 metadata，形成需要人工同步的第二事实源。

`backend/package/uv.lock` 不是 API、worker、镜像或发布流程的安装输入，shipping 依赖由 `backend/uv.lock` 拥有。仓库不维护未被执行入口消费的第二份 backend 依赖闭包；当前依赖声明和 shipping 锁不包含 `igraph` 或 `pymupdf`。

`langgraph-cli[inmem]` 只提供本地 LangGraph CLI 开发服务器；shipping API 直接启动 FastAPI，worker 直接启动 ARQ，Compose、Dockerfile、workflow、Makefile 与脚本都没有 CLI consumer。它及仅由其引入的 `langgraph-api` 因此不属于生产依赖闭包；审计发现该闭包漏洞后删除直接依赖并重新生成 shipping lock，不用忽略 advisory 掩盖无 consumer 的攻击面。

## 后果

- 漏洞数据库与包许可证元数据会变化，网络故障也会导致 gate 失败；失败必须保留工具输出，不静默降级。
- 许可证报告不构成法律意见，也不自动阻断；Review 需要结合分发方式与上游许可证文本判断。
- Python 锁文件升级会改变运行依赖；升级只处理审计直接发现且已有兼容修复版本的包。Node.js override 只处理已知高危项。
- 新增 manifest 或锁文件时必须同步更新 dependency audit 的路径过滤，否则该 Owner 的变化不会自动触发审计。
- 仓库不再提供 `langgraph dev/up/build` 入口；只有新增被 Review 接受的 CLI 开发或部署流程，并同时拥有版本、安全与启动契约时，才可重新引入 `langgraph-cli`。

## 验证

运行 `make audit-dependencies`、`make audit-licenses`、`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`、backend unit、web lint/unit/build、docs build 与 `git diff --check`。`make audit-dependencies` 同时执行固定脆弱 fixture，验证审计 gate 的失败路径。对 CLI 删除另以源码搜索确认 shipping 启动面不存在 `langgraph dev/up/build` consumer，并用 `uv audit` 验证重新锁定的生产闭包无已知漏洞。
