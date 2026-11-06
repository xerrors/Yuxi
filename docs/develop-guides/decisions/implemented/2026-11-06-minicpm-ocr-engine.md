# MiniCPM-OCR 自托管引擎接入

状态：implemented
类型：feature
Owner：backend/yuxi/infrastructure/document_parsing/engines/minicpm_ocr.py

## 问题

知识库需要在不部署本地 GPU 的前提下使用 OCR 能力，issue #266 提出用多模态模型替代依赖 GPU 的本地 OCR。MiniCPM-OCR 通过 vLLM 提供 OpenAI 兼容接口，但项目现有引擎没有该模型的请求协议：它需要 `mm_processor_kwargs` 切片参数、关闭 thinking，并使用与通用 VLM 不同的解码参数；缺少这些参数时请求要么被服务端拒绝，要么静默降低识别质量。

## 决策

### 实现方案

`minicpm_ocr` 作为普通远端引擎注册进 `ENGINE_SPECS`，复用既有 `DocumentEngine` 契约，不引入新抽象。引擎逐页把 PDF 渲染为 PNG（与 `deepseek_ocr` 相同的 pypdfium2 路径和图片锁），单张图片直接读取，然后向配置的 `/v1/chat/completions` 发送 `image_url` + `Text Recognition:` 文本，图片位于文本之前。

请求固定携带官方部署要求的字段：`mm_processor_kwargs`（`downsample_mode` 默认 `4x`、`max_slice_nums` 默认 9）、`chat_template_kwargs.enable_thinking=false`，以及 `temperature=0.01`、`top_p=0.001`、`top_k=1`、`repetition_penalty=1.0`、`no_repeat_ngram_size=100`。`finish_reason` 不是 `stop` 或正文不是字符串时抛出 `DocumentParserException`，沿用 parser 既有的产物发布边界，不产生 `document.md`。

配置由 documents 模块拥有：新增 `minicpm_ocr_opts`（`api_url`、可选 `api_key`、`model`），地址与凭据缺省读 `MINICPM_OCR_API_URL`、`MINICPM_OCR_API_KEY`。未配置 `api_key` 时不发送 `Authorization`，以支持未启用 `--api-key` 的 vLLM 部署。健康检查读取同服务的 `/v1/models`。

服务部署、模型加载和 GPU 资源不属于 Yuxi；Yuxi 只发送页面图片。输出为逐页纯文本，不组装版面结构。

## 替代方案

- 引入 `minicpm-ocr` pip SDK 在进程内执行 PP-DocLayoutV3 版面检测：需要新增 torch/paddle 级依赖和模型下载，与「不要求本机 GPU」的目标冲突，也会显著扩大 API 镜像。
- 只做 DeepSeek OCR 协议兼容：MiniCPM-OCR 缺少切片参数时服务端拒绝或降质，不能靠改模型名复用。
- 把提示词和切片参数暴露为管理员配置：当前没有需要不同取值的真实场景，先固定为官方推荐值，参数仍可由单次解析的 `params` 覆盖。

## 后果

用户需要自行运行 vLLM 服务，并保留 `--trust-request-mm-kwargs`；否则请求会被拒绝。识别按页独立进行，跨页阅读顺序和表格/公式结构不在本引擎内恢复，需要版面输出时应选择 MinerU 或 PaddleOCR 引擎。单页输出超过 `max_tokens` 时整次解析失败而不是发布截断内容。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 请求携带官方解码与切片参数 | 参数缺失导致服务端拒绝或降质 | `minicpm_ocr.MiniCPMOCRParser._call_api` | `pytest test/unit/knowledge/test_minicpm_ocr.py` | 图片与文本顺序颠倒、空 Bearer、自定义切片参数 | Passed |
| 不完整响应不发布产物 | 截断正文被当成成功结果 | `document_parsing.parser` 发布边界 | 同上 | `finish_reason=length`、正文非字符串 | Passed |
| 配置层解析地址/凭据/模型 | 适配器自行读环境或忽略配置 | `documents.options`、`documents.service` | `pytest test/unit/services/test_ocr_service.py` | 未配置地址回落到默认、可选凭据 | Passed |
| 引擎出现在能力注册表 | 前端无法选择或误报支持格式 | `engines.ENGINE_SPECS` | `pytest test/unit/knowledge/test_parser_capabilities.py` | webp 支持集合漂移 | Passed |

本机验证（macOS，Python 3.13，`uv run --no-sync --group test`）：

- `pytest test/unit/knowledge/test_minicpm_ocr.py test/unit/knowledge/test_parser_capabilities.py -q`：20 passed。
- `pytest test/unit/knowledge -q -m "not slow"`：534 passed、1 skipped。
- `pytest test/unit/services/test_ocr_service.py -q`：15 passed，包含新增的 `minicpm_ocr` 环境变量解析用例。
- `ruff check`（改动文件）：通过；`python3 scripts/ci/verify_engineering_contracts.py`：通过。
- `test/integration/api/test_system_router_api.py` 在本机因缺少真实服务栈而 skipped，未计入通过；需要先启动 Compose 后复跑。
- `test/unit/services` 中 5 个 skills/workspace 用例与 `test/unit/config/test_init_noninteractive.py` 的 docker compose 用例在本机失败，stash 本次改动后基线同样失败，与本次改动无关。

真实 MiniCPM-OCR vLLM 服务调用为 Not run（本机无 GPU 与服务），协议层回放不能证明实际识别质量。
