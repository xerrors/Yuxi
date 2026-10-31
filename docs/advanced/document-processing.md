# 文档处理与 OCR

本页供管理员选择 OCR、配置解析服务并排查文档产物。文档解析生成包含 `document.md` 和引用图片的完整目录，图片使用相对路径。知识库把目录发布到 MinIO 后索引；聊天附件随 Input 提交后准备到当前 Project Workdir，Agent 的 OCR 工具也在 Workdir 中生成解析目录。

文件状态和存储归属见[知识库机制详解](../mechanisms/knowledge-base.md)，第一次上传文档见[创建并使用知识库](../intro/knowledge-base.md)。

## 支持的文件

知识库上传接口当前支持：

- 文本：`.txt`、`.md`、`.html`、`.htm`、`.json`、`.csv`；
- Office：`.docx`、`.pptx`、`.xls`、`.xlsx`；
- PDF：`.pdf`；
- 图片：`.jpg`、`.jpeg`、`.png`、`.bmp`、`.tiff`、`.tif`、`.webp`；
- ZIP：压缩包内必须包含 UTF-8 编码的 `.md` 文件。

Office 文件使用本地转换：`.docx`、`.pptx`、`.xlsx` 由 Docling Slim 提取正文与图片，`.xls` 使用 pandas + xlrd 保留工作表内容。图片文件必须启用 OCR；PDF 选择 `disable` 时读取文本层，扫描版 PDF 通常得不到内容。

ZIP 优先选择名为 `full.md` 的文件，否则选择按路径排序的第一个 `.md`，并按完整路径提取正文引用的资源。正文文件存在但内容为空表示合法空白结果；缺少 Markdown 文件仍然失败，MinerU 返回的归档遵循相同规则。绝对路径、`..`、重复归档路径、符号链接和缺失图片均拒绝整个结果。解析失败时回收输出目录。

## 离线 HTML 正文提取

`.html` 和 `.htm` 默认复制正文引用的本地图片；缺失图片或越界路径会使整篇解析失败。对于没有随文件打包图片的离线网页，管理员可以在单文件解析参数中显式设置 `html_text_only: true`。解析器在转换 Markdown 前把 `img` 元素替换为原 HTML 的 `alt` 文本，保留前后正文，不读取本地图片或下载远程图片。上传的 HTML 原件保持完整，图片像素中的文字和图表内容不进入解析结果。

调用 `POST /api/knowledge/knowledge-bases/<kb_id>/documents/parse` 时，正文示例为：

```json
{
  "file_ids": ["<file_id>"],
  "params": { "html_text_only": true }
}
```

该参数随文件的 `processing_params` 保存，只接受布尔值，默认 `false`；它只作用于 HTML，其他格式继续执行原有资源检查。解析成功后另行执行入库，并确认文件状态和检索结果。

## 从 URL 导入网页

网页导入受白名单控制。设置 `YUXI_URL_WHITELIST` 后，系统才会抓取 URL，并把 HTML 转成 Markdown 进入同一套知识库处理流程：

```bash
YUXI_URL_WHITELIST=github.com,docs.example.com,*.wikipedia.org
```

列表以逗号分隔；配置的域名及其子域名可以通过校验，空列表表示关闭 URL 导入。抓取器逐跳验证重定向白名单，最多跟随 5 次重定向，只接受 HTML，响应体默认不超过 10 MiB。每次连接只解析一次 DNS，拒绝包含非公网地址或已知云元数据地址的解析结果，并连接同一组已校验 IP；DNS 失败或无可用地址时直接失败。生产部署仍需按业务配置网络出口限制，抓取边界由 [url_fetcher.py](https://github.com/xerrors/Yuxi/blob/main/backend/yuxi/modules/knowledge/utils/url_fetcher.py)拥有。

这组白名单用于知识库的 `/api/knowledge/files/fetch-url` 导入。Agent 的 `web_fetch` 通过 Tavily 或 DeepSeek 的固定官方接口读取网页，使用独立的[网络搜索配置](./configuration.md#网络搜索与网页正文)，不经过知识库导入抓取器。

## 选择 OCR 方案

| 引擎 | 运行位置 | 输出和适用场景 |
| --- | --- | --- |
| RapidOCR | 本地 CPU | 轻量图片/PDF 文字识别，默认方案 |
| MinerU 官方云服务 | MinerU 云服务 | 使用现有官方 v4 API，无需本地 GPU |
| PaddleOCR-VL-1.6 | 百度 AI Studio 云服务 | 文档版面解析，输出 Markdown |
| PP-OCRv6 | 百度 AI Studio 云服务 | 基础 OCR，输出纯文本 |
| PP-StructureV3 | 百度 AI Studio 云服务 | 文档版面解析，输出 Markdown，与 PaddleOCR-VL、PP-OCRv6 共用配置 |
| DeepSeek OCR | SiliconFlow API | 使用 SiliconFlow 的 DeepSeek OCR 模型 |
| MinerU 自托管 | 可选 GPU 服务 | 社区 4.1 Tasks，文件保留在自行维护的服务内 |

需要轻量部署时，配置云服务凭证即可，不必启用 `all` profile。云服务会接收待解析文件；需要离线或私有化处理时，选择 RapidOCR 或自托管 MinerU。

系统内部引擎 ID 为 `rapid_ocr`、`mineru_ocr`、`mineru_official`、`deepseek_ocr`、`paddleocr_vl_1_6`、`paddleocr_pp_ocrv6` 和 `paddleocr_pp_structure_v3`。页面中的健康状态只表示配置或服务探测结果，真正解析时仍会验证凭证和接口。

登录用户通过 `/api/system/ocr/health` 获取各引擎的状态和固定说明。`configured` 只表示凭据已配置，解析时仍会验证；响应中的 `details` 为空，不包含提供者原始消息、内部服务地址或凭据。配置检查或调用抛出异常时返回 `error`，具体异常记录在服务端日志。

## 在页面配置

管理员进入“设置 → OCR 配置”可以设置默认引擎，并配置自托管服务地址或云端凭证。OCR 配置定义由 documents 模块拥有，system 模块统一管理持久化、脱敏和缓存。知识库上传和附件解析时可以单独选择引擎；没有单独选择时，使用系统默认值。

配置字段优先读取数据库中保存的非空值，缺省时读取对应环境变量或字段默认值。OCR 设置中凭据留空保留原值，填写新凭据会更新，勾选「清除已保存凭据」后保存会清除数据库值并回到环境变量；地址留空也会回到环境变量。API 不会回显环境变量中的密钥；数据库凭证只显示脱敏预览。

配置表单目录和默认引擎由服务端提供。保存失败时共享配置保持原值；上传和解析窗口等待目录加载后选择默认值，已明确选择的引擎不会被迟到的默认值覆盖。页面将 `configured` 显示为“已配置，未验证”。

## 配置各类引擎

### RapidOCR

RapidOCR 随 API/worker 安装，第一次实际解析时加载 ONNX 模型；同一进程的并发首次请求共享实例并串行初始化。模型目录优先使用 `RAPIDOCR_MODEL_DIR`，否则优先使用已安装 wheel 的可写模型目录，不可用时使用用户缓存；默认 PP-OCRv6 模型随 wheel 提供，不需要单独启动服务。

### MinerU 官方云服务

在[官方服务](https://mineru.net/apiManage/docs)申请 API Key，放入 API/worker 的环境：

```bash
MINERU_API_KEY=<your-mineru-api-key>
```

也可以在「设置 → OCR 配置 → MinerU 官方云服务」保存凭证，并选择默认解析模型：Pipeline（`pipeline`，默认）或 VLM（`vlm`）。单次解析显式提供的 `model_version` 优先。

Yuxi 的官方云适配器使用 v4 上传链接、批处理状态和结果下载接口。社区 4.1 Tasks 使用另一套端点、参数和结果字段，因此两者分别选择引擎，不能仅替换服务 URL。云端额度、文件限制与模型以供应商当前文档为准。

### MinerU 自托管

开发 Compose 的 `mineru-api` 属于 `all` profile，固定安装 MinerU 4.1.3，使用 Torch/vLLM 后端。构建会下载模型，运行需要兼容 CUDA 13 的 NVIDIA 驱动和足够 GPU 显存；基础镜像与模型命令由 `docker/mineru.Dockerfile` 拥有。上游部署要求见[官方 Docker 文档](https://opendatalab.github.io/MinerU/quick_start/docker_deployment/)。

```bash
docker compose --profile all up -d --build mineru-api
```

API/worker 默认使用 `MINERU_API_URI=http://mineru-api:30001`。外接实例必须提供 [4.1 Tasks 协议](https://opendatalab.github.io/MinerU/zh/usage/tasks/)；健康探测核验 `/v1/health` 的默认任务产物能力和 `/v1/tiers` 的档位。反向代理须返回客户端可访问的同源上传与下载链接。

管理员在「设置 → OCR 配置 → MinerU 自托管」选择默认 `tier`：`flash`、`basic`、`standard`（默认）或 `advanced`。服务必须开放所选档位，附带 Compose 以 `--tier advanced` 开放全部档位。单次解析参数可以覆盖默认值：

```json
{
  "file_ids": ["<file_id>"],
  "params": {
    "ocr_engine": "mineru_ocr",
    "tier": "advanced",
    "ocr_mode": "auto",
    "page_range": "1-5",
    "timeout_seconds": 1800
  }
}
```

`ocr_mode` 接受 `auto`、`txt`、`ocr`；`page_range` 默认 `all`。等待预算包含任务排队和结果下载，默认 1800 秒，也可由 API/worker 的 `MINERU_TIMEOUT` 设置。超时或协议错误会尝试取消已知任务并返回失败；创建结果未知时不自动重试。服务重启后的任务恢复和调用方取消的即时传播不在当前保证范围内。

升级时先更新自托管服务，再重建 API/worker 并运行 Compose 的 `app-init`。初始化幂等迁移数据库中 `mineru_ocr_host_opts` 的旧 `backend`/`effort`：Hybrid + high 转为 `advanced`，其余已支持组合转为 `standard`；已有 `tier` 优先，服务地址保留。这是计算投入的迁移政策，不保证新旧结果质量等价。单次解析只读取上列新协议参数。旧 `/file_parse` 服务必须升级后才能继续解析。

### DeepSeek OCR

在“智能体 → 模型供应商”中启用 `siliconflow-cn` 并配置凭证。DeepSeek OCR 会复用该供应商的 API 地址和 API Key，不接受单独的 OCR 凭证配置。

### PaddleOCR API

在[百度 AI Studio Access Token 页面](https://aistudio.baidu.com/account/accessToken)获取 Access Token：

```bash
PADDLEOCR_API_TOKEN=<your-paddleocr-token>
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
```

`PADDLEOCR_API_URL` 可省略，默认使用上面的地址。`PaddleOCR-VL-1.6`、`PP-OCRv6` 和 `PP-StructureV3` 共用这组配置。两个版面模型输出 Markdown 和图片，PP-OCRv6 输出纯文本；接口及模型见[官网说明](https://www.paddleocr.ai/latest/en/version3.x/inference_deployment/serving/paddleocr_official_api/python.html)。

内置 PaddleX 部署与旧 `/layout-parsing` 外接服务均已移除，`PADDLEX_URI` 不再生效。`app-init` 删除旧 `pp_structure_v3_ocr_host_opts` 配置；旧引擎 ID `pp_structure_v3_ocr` 明确报不支持，不自动转换为云端。原默认引擎或文件解析设置使用该 ID 时，管理员须重新选择云端 PP-StructureV3 或其他引擎，再执行解析。

## 分块参数和配置快照

知识库的分块配置由两部分组成：`chunk_preset_id` 表示策略，`chunk_parser_config` 保存该策略的具体参数。文件级 `processing_params` 会保存本次使用的 `ocr_engine`、分块策略和参数快照；OCR 服务地址和凭证在执行时读取，不写入文件快照。

修改分块策略不会自动重写已经索引的文件。要让旧文件使用新策略，需要重新解析或重新入库，并在最后确认文件状态和检索结果。

## 图片访问

知识库发布阶段把图片保存到私有 `kb-images` bucket，通过带知识库权限校验的后端路径访问。图片与 Markdown 按文档和解析尝试归属，持久化新结果后回收旧产物；失败只回收本次尝试，删除文档回收该文档的全部解析资源。不要把 MinIO 对象地址直接写成公开 URL，也不要为方便预览而开放整个 MinIO 管理端口。头像等公开图片使用 `/minio/public/...` 同源只读代理，二者边界不同。

Web 的 PDF/图片预解析先保存在用户隔离的临时目录，原文件草稿保存在 MinIO。发送时在 Input 接收事务绑定草稿，随后把原文件及已有解析目录准备到 Project Workdir；就绪提交后回收草稿来源，消费时再绑定正式 Message。未就绪输入不能开始执行。正式图片通过当前 Thread 的鉴权 artifact 接口读取，完整时序和调用格式见[图片与文件附件](./agents-public-api.md#图片与文件附件)。

草稿删除只清理尚未绑定的来源；正式附件在输入已消费或取消、文件已就绪且会话没有活动 Run 时才允许删除。正式删除先提交记录删除，再清理原文件和该附件的解析目录；清理失败时记录已删除，需要按日志处理剩余文件。

## 文件限制与排查

- 知识库和工作区单个上传文件最大 100 MiB；聊天文件草稿使用独立的 5 MiB 上限，不适用工作区上传上限。
- 工作区一次最多上传 50 个文件；知识库批量处理使用任务和页面限制。
- Agent 的 `read_file` 直接读取 UTF-8 文本和图片；PDF、Office 或其他二进制文件先用 `ocr_parse_file` 生成 Markdown。
- OCR 服务不可用时，先在 OCR 配置页查看健康状态，再检查容器日志、API 地址和凭证。
- 解析完成不等于索引完成；回到知识库文件列表确认 `parsed` 或 `indexed`，再做检索验证。

详细变量和容器映射见 `.env.template`、`docker-compose.yml` 和 `docker-compose.prod.yml`。文档页面只保留影响操作的配置，避免与配置文件维护两份默认值。
