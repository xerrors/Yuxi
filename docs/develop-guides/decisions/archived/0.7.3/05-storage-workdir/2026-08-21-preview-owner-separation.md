# 分离 Workspace 与 Knowledge Preview Owner

状态：archived
类型：simplification
Owner：backend/package/yuxi/knowledge/preview.py

## 问题

`yuxi.workspace.preview` 同时拥有通用格式识别、Office 转换、Workspace 本地缓存和被 Knowledge Preview 复用的渲染策略。Knowledge Preview 实际从 MinIO 读取原始对象，并把 Office PDF 缓存持久化到 Knowledge bucket；它与 UserWorkspace 文件系统 Preview 具有不同的身份、存储和生命周期 Owner。当前反向依赖使 `workspace` 成为全局 Preview 工具箱。

## 决策

通用文件格式识别、文本渲染、媒体类型检测、大小策略、`PreviewResult` 和无存储状态的 Office 转换进入 `yuxi.utils.filepreview`；渲染入口直接返回 `PreviewResult`。`yuxi.workspace.preview` 只拥有 UserWorkspace 或已授权临时文件字节到 Preview 的编排及 runtime 本地 Office 缓存。新增 `yuxi.knowledge.preview`，直接拥有 Knowledge metadata、MinIO 原始对象读取和 `{kb_id}/preview/{file_id}.pdf` 持久缓存。

删除 `KnowledgeBase.read_file_preview()` 及相关私有 Preview 方法。Workspace Knowledge 路由在完成权限校验后直接调用 `yuxi.knowledge.preview.read_knowledge_file_preview()`；不保留薄代理或旧位置 re-export。Workspace、Viewer 与 Artifact 的 HTTP 用例先在各自 Owner 完成授权和有界读取，再通过 `yuxi.services.file_preview` 把字节交给 Workspace runtime preview；Artifact 不把临时路径、授权或下载语义交给 Preview 层。既有 HTTP payload、binary headers、大小限制和 Office 缓存行为保持。

## 替代方案

- 保持 `workspace.preview` 为跨存储共享工具箱：让 Knowledge 的 MinIO 持久缓存依赖 Workspace runtime cache，拒绝。Artifact 只在自身授权和有界读取后复用临时 runtime preview，不转移来源存储 Owner。
- 只移动共享函数，不新增 `knowledge.preview`：MinIO Preview 生命周期继续埋在 `KnowledgeBase` 大类中，两个 Preview 用例仍不对称，拒绝。
- 新增统一 Preview Service：会重新合并文件系统与 MinIO 两种存储生命周期，拒绝。

## 后果

移动函数改变测试 monkeypatch 位置和惰性 Knowledge import 链。实现同步迁移全部 consumer 与测试，不增加兼容 alias。Knowledge 路由继续在实际请求发生时惰性导入解析等重运行时。Artifact runtime preview 使用派生缓存，授权、原始字节和临时文件清理仍由 `artifact_service` 拥有。

真实 Knowledge HTTP integration 与 Artifact Office 转换链路尚未运行，因此不能用 Router unit 或 Markdown preview 代替对应 wire contract 验证；该范围继续明确记录为 `Not run`。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 通用渲染原语不依赖 Workspace、Knowledge、MinIO 或 FastAPI | `utils.filepreview` 再次获得用例或存储状态 | `yuxi.utils.filepreview` | import/source 检查；utils unit | 导入任一领域或 HTTP 模块时拒绝 | Passed |
| Workspace Preview 只拥有本地或已授权临时字节预览和 runtime cache | Knowledge/MinIO 逻辑回流 Workspace，或 Artifact 绕过自身授权 | `yuxi.workspace.preview`、`yuxi.services.artifact_service` | Workspace/Artifact preview unit；consumer 搜索 | Knowledge 导入 `workspace.preview` 或 Artifact 把授权下沉到 Preview 时拒绝 | Passed |
| Knowledge Preview 直接拥有 MinIO 读取和 PDF 缓存 | `KnowledgeBase` 保留薄入口或 Workspace cache 被 KB 复用 | `yuxi.knowledge.preview` | Knowledge preview unit；直接 Router unit | 第二次 Office preview 命中 MinIO cache 且不再次转换 | Passed |
| 既有 Preview wire contract 保持 | 二进制响应、大小限制或错误映射漂移 | Workspace router 与 Preview services | Workspace/Viewer/Knowledge unit 和 HTTP integration | 超限文件不下载正文；folder 预览失败 | Not run |

旧能力不存在：Knowledge 不导入 `yuxi.workspace.preview`；Artifact 不直接导入该模块，也不把来源授权、持久缓存或临时文件生命周期交给 Preview；`KnowledgeBase` 不保留 `read_file_preview()`、`_ensure_office_pdf_preview()`、`_office_pdf_preview_path()` 或 `_get_minio_file_size()`；旧模块不 re-export 已移动的通用函数。

重新引入条件：只有 Workspace 与 Knowledge 形成相同的存储身份、缓存生命周期和授权入口时，才重新评估统一 Preview 用例；单纯响应字段相同不构成合并理由。Artifact 只有在授权仍由自身 Owner 执行、Preview 只接收有界字节时才继续复用 runtime adapter。

当前证据：Preview、Artifact 与 package import unit、全量 backend unit、Artifact Markdown HTTP preview、工程 verifier、Ruff 与 `git diff --cached --check` 通过。真实 Knowledge HTTP integration 与 Artifact Office 转换链路尚未运行，对应 wire contract 保持 `Not run`。
