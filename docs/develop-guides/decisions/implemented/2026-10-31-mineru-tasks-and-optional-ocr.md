# MinerU Tasks 接入与可选 OCR 部署收敛

状态：implemented
类型：simplification
Owner：backend/yuxi/infrastructure/document_parsing/engines/mineru.py

## 问题

自托管 MinerU 固定在 3.4.4，旧同步协议不能调用 4.1 服务。项目同时维护 PaddleX GPU 镜像和流水线，而云端 PaddleOCR 已覆盖主要解析场景。官方 MinerU 云服务公开 v4 协议与社区 4.1 Tasks 不同，不能仅通过切换 URL 合并。

## 决策

### 实现方案

Compose 保留按需启用的 MinerU，固定安装 4.1.3 并使用官方 Torch/vLLM 模型与新命令。移除内置 PaddleX 服务、构建材料和失效默认地址；PP-Structure HTTP 引擎保留，要求显式配置外部服务。

MinerU 引擎按单文件执行 Tasks 创建、原始字节上传、状态查询和 ZIP 下载。仅完整成功产物进入既有 Markdown 与图片发布边界。创建不自动重试；创建、上传、查询和下载共享 asyncio 总时限，持续缓慢响应也会中断；超时尝试取消已知非终态任务并明确报错，清理另限 10 秒。业务状态仍归 Yuxi，跨 worker 重启恢复、即时取消传播和多文件批处理不在范围内。

在[全局默认配置决定](./2026-10-23-mineru-default-options.md)之上，documents 拥有持久设置迁移，解析基础设施共享旧参数映射：已保存的 Hybrid high 转为 advanced，其余已支持组合转为 standard；显式新 tier 优先，保留地址和其他字段。启动装配在既有配置同步事务中幂等迁移，历史单次参数在 MinerU 引擎入口归一化。该政策保留计算投入意图，不宣称新旧质量等价。官网云端适配和默认 RapidOCR 保持独立。

用户文档优先介绍云端配置，再说明可选私有部署。首次范围限于现有 PDF/图片，Office 路由与结构化引用不扩展。

## 替代方案

- 保留旧部署：维护负担不变，无法支持社区 4.1。
- 全部改云端：改变文件去向且失去离线/私有化部署，超出授权条件。
- 加独立 file-parse 包装服务：新增进程和恢复存储，仍需处理超时查询，不适合已有后台作业体系。
- 删除 PP-Structure 引擎：破坏外接服务与持久配置消费者；只删除内置 GPU 部署即可实现目标。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 新协议生成完整 Markdown 与图片 | 错认 202/失败状态为产物、下载错误文件 | MinerU 引擎、artifacts | 协议 unit、真实 HTTP E2E、4.1.3 探针 | 缺结果、ID 错配、失败/超时、坏 ZIP、持续滴流、同步解包超时 | Passed |
| 旧设置迁移幂等且保留地址 | 高强度被默认值覆盖、管理员值丢失 | documents 配置、bootstrap/options | 真实 PostgreSQL 迁移与 HTTP 配置测试 | 非法旧组合、新 tier 优先、重跑 | Passed |
| 无 PaddleX 内置部署，外接仍可用 | 悬空默认地址、误删除接入能力 | Compose、PP-Structure 引擎 | Compose 配置与相关 unit | 未配置地址明确不可用 | Passed |
| 可选 MinerU 可按文档部署 | 镜像依赖、命令或健康端点不匹配 | Dockerfile、Compose | 构建/运行探针、部署契约检查 | 旧命令与版本负向搜索 | Passed |

独立 Compose 测试环境使用独立 PostgreSQL、Redis、MinIO、Milvus、Neo4j、文件目录及宿主端口，挂载本工作树源码；API/Web 复用已构建的依赖镜像，MinerU 镜像按本次 Dockerfile 构建。以下命令省略本地隔离环境的 Compose 配置参数：

- `docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow'`：3526 passed、125 skipped、7 subtests passed；跳过项不计为通过。
- 主机 `pytest backend/test/unit/config -q`：207 passed，覆盖容器内没有仓库根目录而跳过的部署配置检查。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/services/test_app_initialization.py -q`：9 passed，独立 PostgreSQL 回读迁移后的值、审计归属和重跑结果。
- `docker compose exec -T api uv run --no-sync --group test pytest test/integration/api/test_system_router_api.py -k 'mineru_defaults or config_option_update' -q`：3 passed。完整该文件为 15 passed、1 failed；已有权限用例的精确配置列表漏掉三个搜索供应商选项，与本次 OCR 变更无关。
- `docker compose exec -T api uv run --no-sync --group test pytest test/e2e/test_document_parsing_artifacts_e2e.py -q`：4 passed，覆盖草稿配置、完整附件目录、真实 worker 的知识库发布、重试和回收。
- `docker compose exec -T frontend pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`：通过，994 tests passed。`playwright-cli run-code --filename=frontend/test/browser/mineruOptions.js`：保存、重载、1440 浅色与 768 深色截图通过。
- `docker compose --profile all build mineru-api`：构建与两组模型校验通过；启动后 `/v1/health` 返回 4.1.3，`/v1/tiers` 包含四个档位。真实扫描 PDF 经 `parse` 生成的 Standard/Advanced 目录独立回读通过。
- `python3 scripts/ci/verify_engineering_contracts.py`、`python3 -m unittest scripts.ci.test_verify_engineering_contracts`、`pnpm --dir docs run build`、任务 Python 文件 Ruff 与 `git diff --check`：通过。

旧能力不存在：内置 paddlex 服务、Dockerfile 与流水线 YAML 被删除；自托管 MinerU 不再调用旧 /file_parse。历史 changelog 与归档记录保留其时间语义。

重新引入条件：只有出现当前云端和外接服务不能满足、且有维护 Owner 的部署需求时，才重新评估内置 PaddleX。

## 后果

GPU 镜像与模型下载仍需要网络、磁盘与兼容驱动，默认部署不启用该服务。MinerU 任务索引不保证服务端重启恢复，创建结果未知时不能盲目重试。云端与自托管协议独立维护；外接 PP-Structure 的部署生命周期由管理员负责，旧内置服务的默认引擎设置不会被自动改写。

真实探针使用 NVIDIA RTX 4090、驱动 595.91.07、vLLM 0.21.0、MinerU 4.1.3。Standard 与 Advanced 对一页扫描 PDF 生成的 `document.md` 均包含独立预置的验证文字。该证据不代表复杂表格、图片分析质量、性能或其他 GPU 的兼容性结论。未提供云端凭据，真实官方云端与外接 PP-Structure 调用为 Not run。
