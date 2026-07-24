# 文档处理与 OCR

Yuxi 将上传文件先保存为原文件，再解析为 Markdown 并按知识库分块策略入库。管理员可在“设置 → OCR 配置”选择默认 OCR 方法，并维护确有需要的服务地址和云端凭证；知识库上传或临时附件解析仍可逐次选择方法，未显式选择时使用系统默认项。

## 支持的文件类型

### 常规文档

| 类型 | 格式 | 说明 |
|------|------|------|
| 文本 | .txt, .md, .html, .htm | 直接提取内容 |
| Word | .docx | 保留格式和结构 |
| PowerPoint | .pptx | 保留主要文本结构 |
| PDF | .pdf | 支持文本和图片 PDF |
| 表格 | .csv, .xls, .xlsx | 识别表格结构 |
| JSON | .json | 结构化数据 |

### 图片文件

图片文件必须选择 OCR 引擎才能提取文字：
- .jpg, .jpeg, .png, .bmp, .tiff, .tif

### 压缩包

支持上传 ZIP 压缩包，系统会：
- 自动提取并处理其中的 Markdown 文件
- 处理图片并上传到对象存储
- 智能识别 `full.md` 或第一个 `.md` 文件

### 网页内容

知识库支持先从 URL 抓取页面内容，再作为文件进入现有上传、解析与入库链路：

1. 配置 `YUXI_URL_WHITELIST` 环境变量启用白名单机制
2. 系统自动将 HTML 转换为 Markdown
3. 内置去重机制，避免重复抓取

::: tip URL 白名单配置
示例：`YUXI_URL_WHITELIST=github.com,*.wikipedia.org,docs.python.org`
:::

## OCR 方案选择

系统提供多种 OCR 方案，适用于不同场景：

### 方案对比

| 方案 | 适用场景 | 硬件要求 | 特点 |
|------|----------|----------|------|
| RapidOCR | 基础文字识别 | CPU | 免费开源，速度快 |
| MinerU | 复杂 PDF、表格 | GPU | 精度高，版面分析好 |
| MinerU Official | 复杂文档 | 无 | 官方云服务，开箱即用 |
| PP-Structure-V3 | 表格、票据 | GPU | 专业版面解析 |
| DeepSeek OCR | 智能理解 | 无 | 复用 SiliconFlow 模型供应商，Markdown 输出 |
| PaddleOCR-VL-1.6 | 复杂文档、表格、图片 PDF | 无 | 百度 AI Studio 云端服务，输出 Markdown |
| PP-OCRv6 | 基础文字识别 | 无 | 百度 AI Studio 云端 OCR，输出纯文本 |

后端保存的引擎标识与界面名称对应如下：`rapid_ocr`、`mineru_ocr`、`mineru_official`、`pp_structure_v3_ocr`、`deepseek_ocr`、`paddleocr_vl_1_6`、`paddleocr_pp_ocrv6`。

### 选择建议

- **个人使用或 CPU 环境**：选择 RapidOCR，免费且资源占用低
- **高精度需求**：选择 MinerU（需要 GPU）或 MinerU Official
- **表格密集型文档**：选择 PP-Structure-V3
- **云端版面解析**：选择 PaddleOCR-VL-1.6，适合希望输出 Markdown 的 PDF 或图片文档
- **云端纯文字识别**：选择 PP-OCRv6，适合只需要提取图片文字的场景
- **简单云服务**：选择 DeepSeek OCR 或 PaddleOCR API

## 快速配置

管理员打开“设置 → OCR 配置”后，可以：

- 选择全局默认 OCR 方法
- 配置 MinerU、PP-Structure 等自托管服务端点
- 每个服务使用独立卡片；非编辑状态以禁用输入框展示当前数据库值或环境变量来源，敏感字段只显示脱敏预览
- 点击卡片右上角“编辑”后修改配置；取消不会修改，留空保存会清除数据库值并改为读取环境变量

DeepSeek OCR 固定复用 `siliconflow-cn` 模型供应商的 API 密钥与 Base URL，不显示独立配置表单。其他服务配置保存在通用 `config_options` 表中：每次运行时读取都会查询数据库，数据库非空值优先，字段为空时读取对应环境变量。API Key / Token 允许明文写入数据库，这是明确的部署取舍；需要脱敏的字段由定义中的 `sensitive` 元数据显式标记。读取接口不会回显密钥原文：数据库值只返回真实首尾字符的脱敏预览，环境变量只返回配置来源，前端不会获得环境变量内容。

附件添加和知识库解析统一使用 OCR Selector。Selector 每次展开都会刷新全部 OCR 方法的健康状态；可用方法直接显示标题和状态，不可用方法默认折叠，管理员可通过右上角“去配置”直接打开 OCR 配置页。后端系统配置仍允许将默认值设为 `disable`，但 Selector 默认不展示该选项；需要显示时必须由调用方显式开启。

### RapidOCR

启动后会默认下载，无需配置

### MinerU（高精度）

项目已内置 mineru-api 服务（位于 docker-compose.yml，属于 all profile），无需额外下载官方 compose 文件。首次构建镜像时会基于 docker/mineru.Dockerfile 下载模型，该过程耗时较长。

启动服务（需要 GPU）：

```bash
docker compose --profile all up -d --build mineru-api
```

该服务在 `30001` 端口提供 `/file_parse` 接口，后端 `api` / `worker` 默认通过 `MINERU_API_URI=http://mineru-api:30001` 连接，通常无需额外配置。

::: tip 显存不足
若显存有限导致启动失败，可在 `docker-compose.yml` 的 `mineru-api` 服务下放开 `--gpu-memory-utilization` 参数（如 `0.5`，必要时进一步降低）。
:::

### MinerU Official（云服务）

从 [MinerU 官网](https://mineru.net) 获取 API 密钥，在 .env 配置环境变量

```env
MINERU_API_KEY=your-api-key-here
```

### PP-Structure-V3（结构化）

启动服务（需要 GPU）

```bash
docker compose up paddlex -d
```

### DeepSeek OCR（简单云服务）

在模型供应商配置中启用 `siliconflow-cn` 并配置 API 密钥。DeepSeek OCR 会复用该供应商的 Base URL 和凭证，不需要额外配置。

### PaddleOCR API（百度 AI Studio 云服务）

PaddleOCR API 使用百度 AI Studio 的 Access Token。获取方式：

1. 登录 [百度 AI Studio Access Token 页面](https://aistudio.baidu.com/account/accessToken)
2. 在页面中复制 Access Token
3. 在 `.env` 中配置为 `PADDLEOCR_API_TOKEN`

```env
PADDLEOCR_API_TOKEN=your-access-token-here
```

如需使用自定义 PaddleOCR API 地址，可额外配置：

```env
PADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs
```

配置完成后，重启后端服务，在上传文件或解析临时附件时可以选择：

- `PaddleOCR-VL-1.6`：对应 `paddleocr_vl_1_6`，用于文档版面解析，返回 Markdown
- `PP-OCRv6`：对应 `paddleocr_pp_ocrv6`，用于基础 OCR，返回按行拼接的纯文本

## 解析参数与分块快照

知识库分块配置由两部分组成：`chunk_preset_id` 只表示策略（`general`、`qa`、`book`、`laws`、`semantic`、`separator`），具体参数统一放在 `chunk_parser_config` 中。不要再写入旧的根级 `chunk_size`、`chunk_overlap` 或 `qa_separator` 字段。

文件级 `processing_params` 保存 `ocr_engine`、分块策略和 `chunk_parser_config`。OCR 的连接端点和凭证在执行时从通用配置或环境变量读取，不写入文件快照。

## 图片显示配置

上传文档中的图片需要正确配置才能在外部显示：

在 `.env` 中设置服务器 IP：

```
HOST_IP=your_server_ip
```

## 注意事项

1. **图片文件必须启用 OCR**：否则无法提取内容
2. **GPU 要求**：MinerU 和 PP-Structure-V3 需要 GPU 支持
3. **API 密钥**：DeepSeek OCR 复用 `siliconflow-cn` 模型供应商凭证；MinerU Official 和 PaddleOCR API 需要各自的 API 密钥或 Access Token
4. **超时处理**：复杂文档解析可能耗时较长，可通过 `MINERU_TIMEOUT` 环境变量调整超时时间
5. **文件大小限制**：知识库与工作区的单个上传文件大小均不超过 100 MB；工作区一次最多上传 50 个文件
6. **解析配置**：文件只保存当次 `ocr_engine` 与分块参数快照；端点和凭证执行时使用最新通用配置或环境变量
7. **Agent 读取非文本文件**：Agent 的 `read_file` 只直接读取 UTF-8 文本和图片；遇到 PDF、Office 或其他二进制文件时，应使用 `ocr_parse_file` 生成 Markdown 后再读取
