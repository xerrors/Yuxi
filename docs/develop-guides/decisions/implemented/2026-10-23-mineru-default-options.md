# MinerU 解析后端与强度默认配置

状态：implemented
类型：feature
Owner：backend/yuxi/modules/documents/options.py

当前仍保留 documents 拥有全局默认配置、单次参数优先的决定。自托管协议、`backend`/`effort` 字段和 `/file_parse` 已由 [MinerU Tasks 接入决定](./2026-10-31-mineru-tasks-and-optional-ocr.md)替代；下文对应细节与验证属于 3.x 实施时的记录。

## 问题

管理员不能在 OCR 设置中选择自托管 MinerU 的解析后端与强度。解析请求没有发送 effort，Hybrid 使用上游 medium 默认值时关闭图片与图表分析。

## 决策

### 实现方案

在现有 mineru_ocr_host_opts 中增加 backend（hybrid-engine / vlm-engine）和 effort（medium / high），默认 Hybrid + medium。复用 Option 的 PostgreSQL 持久化、choices 校验与启动定义同步，不增加 Schema、环境变量或新的配置项。

documents service 在统一解析入口读取管理员配置，作为 MinerU parser 的构造参数；parser 在发送现有 /file_parse 请求时优先读取显式任务参数，缺省时使用实例配置，并透传 effort。OCR 设置页按 choices 渲染下拉框，从字段默认值显示尚未保存的选项；帮助文案解释 effort 仅作用于 Hybrid。

范围不包含 MinerU 升级、官方云 API、图片分析开关或新增单次上传配置界面。现有 image_analysis=true 保持不变。

## 替代方案

- 单次上传增加高级表单：不能覆盖使用统一解析入口的其他 consumer，且超出全局默认配置需求。
- 独立配置模型或接口：复制现有 Options 的存储、授权和失效机制，没有当前必要性。
- 默认 high：能启用 Hybrid 图片分析，但增加所有默认解析的成本；保留 medium，由管理员显式选择 high。

## 后果

既有部署通过 app-init 同步字段定义，无需 Schema 迁移；API 与 worker 重启后加载新代码。未保存过新字段的配置使用 Hybrid + medium，管理员显式选择 high 才改变默认解析成本。强度对 VLM 不生效，界面保留明确说明。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| backend/effort 可保存回读且拒绝非法枚举 | 配置仅展示或保存非法值 | documents/options.py、system/services/options.py | test_mineru_defaults_round_trip_and_reject_invalid_choices：真实 HTTP 保存、PG 回读 | 非法 backend/effort 返回 400 且原值不变 | Passed |
| 管理员默认值到达 MinerU 请求，任务显式值优先 | 默认值未读取或 effort 丢失 | documents/service.py、engines/mineru.py | service/parser unit；test_mineru_options_reach_draft_parser_and_preserve_artifacts：2 passed，读取 multipart 字段及 Workspace Markdown/PNG 字节 | 实现前回归测试因缺字段、未透传 effort、未接收默认参数而失败 | Passed |
| 设置页显示默认值、编辑保存并重载 | 缺省值空白或错误渲染成文本输入 | OCRSettingsSection.vue | ocrSettings.test.js；真实页面 mineruOptions.js：保存、刷新、浅深主题与 1440/768 宽度 DOM 和截图 | 只有地址的旧记录仍显示 Hybrid + medium；保存失败保留草稿与原值 | Passed |

在挂载当前源码的现有 Compose 测试环境执行，预置登录凭据失效时使用独立创建并回收的临时账号。实际命令与结果：

- `uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`：3114 passed、107 skipped；容器未挂载仓库根目录等既有跳过不计为验证通过。
- `uv run --no-sync --group test pytest test/integration/api/test_system_router_api.py -k 'config_option or ocr_options or mineru_defaults' -q -p no:cacheprovider`：新配置 round-trip 与服务地址用例通过；既有权限用例因精确配置列表遗漏现有搜索配置而失败。
- `uv run --no-sync --group test pytest test/e2e/test_document_parsing_artifacts_e2e.py::test_mineru_options_reach_draft_parser_and_preserve_artifacts -q -p no:cacheprovider`：2 passed。
- `pnpm run lint:check`、`pnpm run test:unit`、`pnpm run build`：通过，前端 808 tests passed；`playwright-cli run-code --filename=frontend/test/browser/mineruOptions.js`：真实页面验证通过。
- `python3 scripts/verify_engineering_contracts.py`、`python3 -m unittest scripts.test_verify_engineering_contracts`：通过；文档 `pnpm run build`：通过；任务 Python 文件 Ruff 与 `git diff --check`：通过。

扩大验证的既有完整附件发送 E2E 在解析与资源断言之后失败：旧 queue_paused 赋值未阻止 Input 消费，取消 pending Input 返回 409；该用例与输入队列实现不属于本决定的修改范围。

确定性 replay 只证明协议与产物链路。真实 GPU MinerU 服务未运行，图片识别质量与性能校准为 Not run；官方云 API 不在本次实现范围。
