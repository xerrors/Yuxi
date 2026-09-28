# 决策方案与 Python 阅读顺序

状态：implemented
类型：process
Owner：docs/develop-guides/decisions/README.md

## 问题

决策记录容易只保留原则，缺少可供审查的实现方案；旧 Python 文件的布局容易使新增代码继续违反向下规则。贡献者需要明确方案粒度和局部修改范围。

## 决策

Decision 的提案或决策节包含凝练的实现方案；Python 向下规则适用于本次新增或实质修改的代码，并允许为降低认知负担提取单次调用的 helper。

### 实现方案

[Decision 格式](../README.md)拥有方案内容与生命周期要求，在现有提案或决策节内说明模块职责、主链路和关键边界；[后端约定](https://github.com/xerrors/Yuxi/blob/main/backend/AGENTS.md)拥有阅读顺序、必要定义依赖和函数拆分规则。保留现有一级章节和验证器，不批量改写历史记录或重排旧代码；独立 Reviewer 检查内容是否足够具体及改动是否局部。

## 替代方案

仅强调遵守旧规范仍缺少执行尺度；新增必填顶层章节及静态检查会扩大历史文档迁移，而标题存在不能证明方案质量。

## 后果

新增或实质更新的记录提供可审查的简要方案，相关 Python 改动按高层到细节组织。历史记录和无关代码保持原样。方案质量与阅读顺序由语义 Review 判断，机械检查只验证结构、链接和预算。

## 验证

- Inspected：独立 Reviewer 对照完整变更与规范核查；仅写“交给 service”及照搬旧 helper 布局两个反例均违反明确条款。
- Passed：`python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`（62 项）、`cd docs && pnpm run build`、`git diff --check`。文档构建保留 bundle 体积提示。
- 未通过：`docker compose exec -T api uv run --group test pytest test/unit -m "not slow"` 因容器内 editable 依赖文件权限失败；改用 `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m "not slow"`，2359 passed、58 skipped、2 failed。两项失败为 Docker/Kubernetes ephemeral sandbox 的 runtime profile 配置断言；`docker compose exec -T api uv run --no-sync --group test pytest test/unit/backends/test_sandbox_provisioner_config.py -q --tb=short -p no:cacheprovider` 复现相同失败（78 passed、2 failed）。本变更仅修改规范，不修复该产品测试问题。
