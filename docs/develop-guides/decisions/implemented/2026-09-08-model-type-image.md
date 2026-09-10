# 模型类型系统新增 image 类型

状态：implemented
类型：feature
Owner：backend/package/yuxi/models/providers/service.py

## 问题

图像生成模型（如 DashScope 的 qwen-image 系列）不支持 OpenAI 兼容 chat 接口，只支持 DashScope 原生 multimodal-generation 接口（`content` 必须是 `[{"text": ...}]` 数组）。但 Yuxi 的模型类型系统只有 chat / embedding / rerank 三种（`VALID_MODEL_TYPES`），图像生成模型被兜底登记为 `chat`，导致两个缺陷：

1. 模型测试按 chat 接口调用，报 `Input should be a valid list: input.messages.0.content`；
2. `get_all_specs("chat")` 把图像生成模型混入纯文本对话智能体的可选模型列表，误选后运行时报同样错误。

## 决策

给模型类型系统正式新增第四种 `image` 类型：

- `VALID_MODEL_TYPES` 加入 `"image"`，`_normalize_model_item` 与 `_normalize_remote_model` 随之接受并保留 image 类型，不再兜底成 chat；
- DashScope builtin provider 的 `capabilities` 加入 `"image"`；
- 前端 ModelProviderManagePanel 的 capabilities 多选、类型 tab、type 下拉均支持「图像生成」；
- `test_model_status_by_spec` 以 `info.model_type == "image"` 走原生接口测试，不再靠 model_id 字符串匹配；
- 图像模型测试**按供应商协议分流**：原生 multimodal-generation 协议当前只有 DashScope 系供应商提供，非 DashScope 的 image 模型显式返回「暂不支持」，不把 DashScope 专用路径拼到它的 base_url 上；
- 测试请求**不显式传 `size`**：不同 Qwen-Image 型号支持的分辨率集合不同（max/plus 只接受文档列出的尺寸），传固定值会让正常模型因参数非法被判不可用；交给模型默认值。

`get_all_specs("chat")`、`model_type == "chat"` 等纯文本模型的分流逻辑不变，image 类型天然不命中这些分支，纯文本模型行为完全不受影响。

## 替代方案

- 仅靠 model_id 字符串匹配特判（原临时修复）：无法让 UI 正确显示「图像生成」，纯文本模型列表仍混入图像模型，且靠猜模型命名，新模型名会漏判；拒绝为正式方案。
- 自动数据迁移脚本：仓库无 alembic 迁移机制，自动改写用户配置数据违背 `ensure_builtin_model_providers_in_db` 的「不覆盖已编辑配置」契约；拒绝。历史数据改为在 UI 中一次性手动纠正（勾选 image 能力 + 改 qwen-image 的 type）。

## 后果

新增 image 类型后，新添加的图像生成模型正确落位，纯文本对话智能体的模型列表不再混入图像模型。历史手动添加的 qwen-image 已通过 UI 把 DashScope 的「能力」勾选 image 并将两个 qwen-image 模型的 type 改为「图像生成」，无需 model_id 兜底。DashScope 图像模型调用仍走 `_test_image_generation_model` 的原生接口（内部将 compatible-mode 域名切回原生域名）。

## 验证

- `backend/test/unit/services/test_model_provider_service.py` 新增 5 用例：image 类型可写入、无 image 能力时拒绝、远端归一化保留 image 类型，以及断言**实际 HTTP payload** 的 2 个（原生 endpoint + 不含 `size`；非 DashScope 供应商不发请求并显式报告不支持）。
- `docker compose exec api uv run --group test pytest test/unit/services/test_model_provider_service.py -q` 通过。
- 浏览器回归：模型管理页 DashScope 能力可勾选 image，qwen-image 测试按钮显示「连接正常」，纯文本 chat 模型列表不再混入图像模型。
