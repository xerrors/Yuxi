# 文档处理与 OCR

Yuxi 支持多种文档格式的智能解析，从简单的文本文件到复杂的 PDF 文档，都能自动提取内容并转换为可检索的格式。

## 支持的文件类型

### 常规文档

| 类型 | 格式 | 说明 |
|------|------|------|
| 文本 | .txt, .md, .html | 直接提取内容 |
| Word | .docx | 保留格式和结构 |
| PDF | .pdf | 支持文本和图片 PDF |
| 表格 | .csv, .xls, .xlsx | 识别表格结构 |
| JSON | .json | 结构化数据 |

### 图片文件

对于图片文件，需要启用 OCR 才能提取文字：
- .jpg, .jpeg, .png, .bmp, .tiff, .tif, .gif, .webp

### 压缩包

支持上传 ZIP 压缩包，系统会：
- 自动提取并处理其中的 Markdown 文件
- 处理图片并上传到对象存储
- 智能识别 `full.md` 或第一个 `.md` 文件

### 网页内容

支持通过 URL 直接抓取网页内容：

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
| DeepSeek OCR | 智能理解 | 无 | 云端服务，Markdown 输出 |

### 选择建议

- **个人使用或 CPU 环境**：选择 RapidOCR，免费且资源占用低
- **高精度需求**：选择 MinerU（需要 GPU）或 MinerU Official
- **表格密集型文档**：选择 PP-Structure-V3
- **简单云服务**：选择 DeepSeek OCR

## 快速配置

### RapidOCR（推荐入门）

```bash
# 下载模型
hf download SWHL/RapidOCR --local-dir ./models/SWHL/RapidOCR

# 配置环境变量
MODEL_DIR=./models

# 启动服务
docker compose up -d api
```

### MinerU（高精度）

```env
# .env 配置
MINERU_VL_SERVER=http://localhost:30000
MINERU_API_URI=http://localhost:30001

# 启动服务（需要 GPU）
docker compose up mineru-vllm-server mineru-api -d
```

### MinerU Official（云服务）

```env
# .env 配置
MINERU_API_KEY=your-api-key-here
```

从 [MinerU 官网](https://mineru.net) 获取 API 密钥。

### PP-Structure-V3（结构化）

```bash
# 启动服务（需要 GPU）
docker compose up paddlex -d
```

### DeepSeek OCR（简单云服务）

```env
# .env 配置（使用已有的 SiliconFlow 密钥）
SILICONFLOW_API_KEY=your-api-key-here
```

## 图片显示配置

上传文档中的图片需要正确配置才能在外部显示：

在 `.env` 中设置服务器 IP：

```env
HOST_IP=your_server_ip
```

## 注意事项

1. **图片文件必须启用 OCR**：否则无法提取内容
2. **GPU 要求**：MinerU 和 PP-Structure-V3 需要 GPU 支持
3. **API 密钥**：部分服务需要额外的 API 密钥配置
4. **超时处理**：复杂文档解析可能耗时较长，可通过 `MINERU_TIMEOUT` 环境变量调整超时时间
