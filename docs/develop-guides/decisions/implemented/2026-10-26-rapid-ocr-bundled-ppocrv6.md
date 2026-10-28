# RapidOCR 使用 rapidocr 包内自带的 PP-OCRv6 模型并在镜像内预置

状态：implemented
类型：bug-fix
Owner：backend/yuxi/infrastructure/document_parsing/engines/rapid_ocr.py

## 问题

解析器固定请求 `OCRVersion.PPOCRV5 + ModelType.MOBILE`，但 rapidocr 任何版本的 wheel 都不含 PP-OCRv5 ONNX（3.5–3.8 捆 PP-OCRv4，3.9 起捆 PP-OCRv6 small，实测 3.8.4 既无 v5 模型也无 `PPOCRV6`/`SMALL` 枚举）。镜像里 `RAPIDOCR_MODEL_DIR` 只是一个新建空目录，因此 v5 模型只能在首次加载引擎时从 modelscope.cn 联网下载；离线部署在 `_load_model()` 失败并落为解析失败，而 `check_health()` 有意不加载引擎、配置页始终显示可用。`pyproject.toml` 的 `rapidocr>=3.0.0` 也低于代码实际要求，非 lock 安装环境会在解析时抛 AttributeError。

## 决策

### 实现方案

`RapidOCRParser._get_model_params()` 请求 `PP-OCRv6` + `ModelType.SMALL`（rapidocr 3.9 wheel 自带的 det/rec 模型；cls 沿用包内 PP-OCRv2 方向分类模型），health 与日志文案同步。`docker/api.Dockerfile` 在创建运行身份时把 site-packages 内 `rapidocr/models/*.onnx` 预置到 `RAPIDOCR_MODEL_DIR` 指向目录，guard 测试同时交叉校验两份 Compose 注入的 `RAPIDOCR_MODEL_DIR` 与镜像预置目录一致（漂移即失败）。依赖下限提升为 `rapidocr>=3.9.0`（v6/SMALL 枚举自 3.9 才有）。健康检查保持"不加载引擎"的行为不变。

## 替代方案

- 构建期预下载 PP-OCRv5 的 det/rec ONNX：拒绝。需为每个 rapidocr 版本维护 URL 与 SHA256，模型仍不在依赖闭包里，3.9.2 仍保留 v5 下载定义，但 wheel 未预置对应模型。
- 给 `~/.cache/rapidocr` 挂持久卷：拒绝。保留运行时网络依赖，只是把首次下载推迟到部署机，离线环境仍失败。
- 让 `check_health()` 真正实例化引擎：拒绝作为本次修复。它能提前暴露问题，但不能让离线环境解析成功，还会让配置页刷新反复加载模型。

## 后果

- OCR 输出代际变化：PP-OCRv6 与 v5 的识别文本不保证逐字一致（官方发布说明称相对 v5 精度提升、CPU 推理更快）。已入库文档不受影响（parse 只在 ingest 执行、markdown 已存 MinIO），只有重新解析使用新模型。
- api 镜像必须与 rapidocr 包内模型文件名保持一致；升级或替换 rapidocr 时 `test_rapid_ocr_models.py` 与镜像预置 guard 会失败，需同步调整。
- 解析链路不再隐含联网要求，离线部署的失败原因回到文件本身（加密 PDF、损坏结构、非法编码等）。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 解析器请求的 det/rec 模型由 wheel 自带，无需下载 | 运行时联网 modelscope 下载，离线失败 | `_get_model_params` | `test/unit/knowledge/test_rapid_ocr_models.py::test_rapid_ocr_requests_models_bundled_in_wheel`（3.9.2 wheel 实测含 `PP-OCRv6_det_small.onnx`/`PP-OCRv6_rec_small.onnx`） | `::test_wheel_bundled_check_rejects_ppocrv5_mobile`：回退 v5+MOBILE 时 wheel 中不存在对应 ONNX，检查在正确原因上失败 | Passed |
| api 镜像预置且与 Compose 注入目录一致 | 目录为空或两处配置漂移使预置失效 | `docker/api.Dockerfile` + Compose `x-api-worker-env` | `test/unit/config/test_docker_compose_service_boundaries.py::test_api_image_preinstalls_bundled_rapidocr_models` | `::test_rapidocr_preinstall_guard_detects_missing_or_mismatched_copy`：删除拷贝步骤、拷错目录、改任一侧 `RAPIDOCR_MODEL_DIR` 时分别报对应缺陷 | Passed |
| 预置目录下加载引擎零网络请求并真实识别 | 首次解析隐式联网 | rapidocr DownloadFile SHA 命中跳过 | 预置 3 个包内 ONNX 到临时目录、`HTTP(S)_PROXY` 指向黑洞后 `RapidOCRParser().process_image("test/data/测试图片.png")` 成功识别中文（模型日志 `File exists and is valid`） | 同条件空目录请求 v5 抛 `DownloadFileException`（modelscope URL）——缺陷存在性 | Passed |
| 依赖下限与代码匹配 | 非 lock 环境安装 3.8 缺枚举/模型 | `pyproject.toml` / lock | `rapidocr>=3.9.0`；lock 同时包含[文本解码](2026-10-26-parser-text-encoding.md)使用的 chardet 直接依赖及 typer marker 规范化；`uv lock --check` 通过 | 3.8.4 wheel 实测无 v6 模型与枚举 | Passed |
| 回归 | 既有解析行为漂移 | `test/unit/knowledge`、boundaries | `pytest test/unit/knowledge` → 390 passed / 1 skipped；rapidocr guard 2 passed；`ruff check`/`format` 干净 | 不适用 | Passed |

未验证范围：无出口网络目标环境的端到端 PDF 入库 `Not run`；v5/v6 同批文档逐份文本对比未做（发布说明口径差异已声明）。
