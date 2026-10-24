# RapidOCR 使用 rapidocr 包内自带的 PP-OCRv6 模型

状态：implemented
类型：bug-fix
Owner：backend/package/yuxi/knowledge/parser/rapid_ocr.py

## 问题

自 2026-05 的 `rapidocr-onnxruntime` → `rapidocr` 迁移起，解析器固定请求 `OCRVersion.PPOCRV5` + `ModelType.MOBILE`，但 rapidocr 任何版本的 wheel 都不含 PP-OCRv5 ONNX（3.5–3.8 捆绑 PP-OCRv4，3.9 起捆绑 PP-OCRv6 small）。`RAPIDOCR_MODEL_DIR` 在镜像里只是一个新建的空目录，也没有持久卷，因此 v5 模型只能在首次加载引擎时从 modelscope.cn 联网下载。

无出口网络的部署环境里，PDF 与图片解析在 `_load_model()` 处失败并落为 `error_parsing`。旧部署之所以看起来正常，是因为模型缓存留在当时那个容器的可写层里；镜像重建后容器可写层丢失，缺陷集中暴露。`check_health()` 有意不加载模型，使引擎在配置页始终显示可用，问题只在真实解析时出现。

## 决策

### 实现方案

`RapidOCRParser._get_model_params()` 请求 `PP-OCRv6` + `ModelType.SMALL`，即 rapidocr 3.9 wheel 自带的 det/rec 模型；cls 沿用包内的 PP-OCRv4 方向分类模型。`docker/api.Dockerfile` 在创建运行身份时，把 site-packages 内 `rapidocr/models/*.onnx` 预置到 `RAPIDOCR_MODEL_DIR` 指向的目录，使镜像自带模型、解析路径不产生网络请求。

健康检查保持"不加载引擎"的行为不变；离线可用性由 guard 测试和镜像构建断言证明，而不是由健康检查冒充。

## 替代方案

- 构建期预下载 PP-OCRv5 的 det/rec ONNX：拒绝。行为与旧部署逐字一致，但要为每个 rapidocr 版本维护 URL 与 SHA256，离线包额外携带 v5 文件，且模型仍不在依赖闭包里。
- 给 `~/.cache/rapidocr` 挂 api/worker 共享持久卷：拒绝。保留运行时网络依赖，只是把首次下载推迟到部署机，离线环境仍然失败。
- 让 `check_health()` 真正实例化引擎：拒绝作为本次修复。它能提前暴露问题，但不能让离线环境解析成功，还会让配置页刷新反复加载模型。

## 后果

- OCR 输出代际变化：PP-OCRv6 与 v5 的识别文本不保证逐字一致（官方发布说明称相对 v5 精度提升、CPU 推理更快）。已入库文档不受影响，只有重新解析才使用新模型。
- 依赖声明遗留：`backend/package/pyproject.toml` 目前仍写 `rapidocr>=3.0.0`，而代码自 3.9 才有效（PPOCRV6/SMALL 枚举、包内模型）。lock 固定 3.9.2 使镜像无暴露，非 lock 安装环境会在解析时 AttributeError；建议后续独立变更把下限提升到 `>=3.9.0`（改 lock 会触发对既有全量漏洞集的审计，不并入本修复）。
- api 镜像必须与 rapidocr 包内模型文件名保持一致；升级或替换 rapidocr 时 `test_api_image_preinstalls_bundled_rapidocr_models` 与 `test_rapid_ocr_requests_models_bundled_in_wheel` 会失败，需同步调整预置步骤。
- 解析链路不再隐含联网要求，离线部署的失败原因回到文件本身（加密 PDF、损坏结构、非法编码等）。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 解析器请求的 det/rec 模型由 rapidocr wheel 自带，无需下载 | 运行时联网 modelscope 下载，离线失败 | `RapidOCRParser._get_model_params` | `test/unit/knowledge/test_parser_facade.py::test_rapid_ocr_requests_models_bundled_in_wheel` | 同文件 `::test_wheel_bundled_check_rejects_ppocrv5_mobile`：回退 PPOCRV5+MOBILE 时捆绑检查在正确原因上失败（3.9.2 wheel 实测无 v5 ONNX） | Passed |
| api 镜像把包内 ONNX 预置到 `RAPIDOCR_MODEL_DIR` 指向目录 | 镜像内 `RAPIDOCR_MODEL_DIR` 为空目录 | `docker/api.Dockerfile` | `test/unit/config/test_docker_compose_service_boundaries.py::test_api_image_preinstalls_bundled_rapidocr_models` | 同文件 `::test_rapidocr_preinstall_guard_detects_missing_or_mismatched_copy`：删除拷贝步骤或拷错目录时 guard 报对应缺陷 | Passed |
| 预置与 Compose 运行时配置同目录 | 两处 `RAPIDOCR_MODEL_DIR` 漂移使预置失效 | boundaries guard | 同文件交叉断言 Dockerfile ENV 与 compose 注入值为同一路径 | 改任一侧目录值时断言失败 | Passed |
| 预置目录下加载引擎零网络请求 | 首次解析隐式联网 | rapidocr DownloadFile SHA 命中跳过 | api 镜像内把包内 3 个 ONNX 拷入模型目录、代理黑洞后实例化 `RapidOCR(params=...)` 成功；同条件空目录请求 PPOCRV5 抛 `DownloadFileException`（modelscope URL） | 缺陷-修复因果双向复现 | Passed |
| 全量后端单测未回归 | 既有解析行为漂移 | `test/unit` | `pytest test/unit -m "not slow"` → 2463 passed（4 项失败为基线既有环境噪声，与本改动无关） | 不适用 | Passed |

未验证范围：未在无出口网络的目标环境执行端到端 PDF 入库，也未对同批 PDF 做 v5/v6 识别文本逐份对比；部署后可用 `docker compose exec api ls -l /home/yuxi/.cache/rapidocr/models` 与实际文档解析复验。
