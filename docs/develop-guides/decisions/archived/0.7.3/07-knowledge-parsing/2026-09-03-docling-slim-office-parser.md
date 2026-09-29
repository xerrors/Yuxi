# 使用 Docling Slim 解析 Office 文档

状态：archived
类型：simplification
Owner：backend/package/yuxi/knowledge/parser/unified.py

## 问题

Yuxi 只使用 Docling 将 DOCX、PPTX、XLSX 和 XLS 转为 Markdown，但完整 `docling` 的 standard 依赖同时安装模型、PDF 管线、Torch、TorchVision 和 IBM Models。后端镜像因此携带当前解析路径不会使用的重运行时。

## 决策

依赖固定为 `docling-slim[format-office,format-pdf-pypdfium2]==2.122.0`，并显式声明 backend 使用的 `pylatexenc`。统一解析器按文件扩展名选择 `MsWordDocumentBackend`、`MsPowerpointDocumentBackend` 或 `MsExcelDocumentBackend`，由 `InputDocument` 校验文件并初始化 backend，再调用公开的 `convert()` 返回 `DoclingDocument`。

对 `InputDocument` 私有 backend 引用的访问只存在于这个适配点，转换结束或失败后统一卸载资源。现有 Markdown 导出、图片上传、顺序替换与失败占位逻辑继续消费 `DoclingDocument`；DOCX 异常仍回退到 `python-docx`，PDF 和图片 OCR 不进入 Office backend。

完整 `docling`、Torch、TorchVision 和 PyTorch 专用索引已从项目依赖删除。镜像补齐 `libreoffice-calc-nogui`，用于将旧 XLS 转换为 XLSX。

## 替代方案

- 保留完整 Docling：无需适配，但继续为未使用的模型和 PDF 能力支付镜像与供应链成本；不采用。
- 使用 AnyDoc：会引入新的解析器、输出差异和维护边界，超出依赖瘦身目标；不采用。
- 为三类 Office 格式分别自研 Markdown 转换：会复制复杂的表格、层级、图片和格式语义；不采用。
- 继续使用 `DocumentConverter`，仅限制 allowed formats：入口仍依赖完整 Docling 的 converter 和 pipeline 装配；不采用。

## 后果

- Office 转换集中在一个直接 backend 适配器，删除未使用的模型与 pipeline 装配层。
- 新 API 镜像为 1,159,075,226 bytes，原镜像为 1,435,192,473 bytes，减少 276,117,247 bytes（19.2%）。
- XLS 的 shipping 环境必须提供 LibreOffice Calc；Slim 版本升级必须重新运行真实格式 fixture。
- `InputDocument._backend` 是上游私有成员，版本升级需要在这一处重新确认初始化和卸载语义。

## 验证

- 容器内 parser 单测：28 passed，覆盖 DOCX、PPTX、XLSX、XLS 真实 fixture、DOCX 图片字节和顺序、上传失败占位、DOCX fallback、PDF 分派与异常资源释放。
- 容器内完整 backend unit：1686 passed，44 skipped。
- 构建后 distributions 回读：`docling-slim==2.122.0`、`pylatexenc==2.11`；完整 `docling`、`docling-ibm-models`、`docling-parse`、Torch 和 TorchVision 均不存在。
- `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts` 通过。
- API 与 worker 使用新镜像启动并保持 healthy，`/api/system/ready` 返回 ready 且未降级。

旧能力不存在：依赖声明、lock、运行环境和构建镜像中不存在完整 `docling`、`docling-ibm-models`、`docling-parse`、Torch、TorchVision 或 PyTorch 专用 source，解析器不再导入或构造 `DocumentConverter`。

重新引入条件：只有 shipping 功能需要 Docling 本地模型或 Docling PDF pipeline，且能证明现有 PDF/OCR 边界无法满足需求，并提供镜像体积、资源、供应链与格式回归证据时，才重新引入完整 Docling 或 Torch 运行时。
