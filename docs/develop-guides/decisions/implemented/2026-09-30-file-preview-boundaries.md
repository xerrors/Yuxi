# 文件预览与原文件读取边界

状态：implemented
类型：architecture
Owner：backend/yuxi/infrastructure/file_preview.py

## 问题

文件预览、下载使用的 MIME 识别和存储读取共用文件结果及字节约束。调用方需要能在各自的授权和存储边界执行预览预算，响应层也需要统一消费业务结果。

## 决策

### 实现方案

`shared/files.py` 拥有 `PreviewResult`、MIME 识别和各入口共用的 30 MB 原文件预览预算。`infrastructure/file_preview.py` 拥有展示类型判断、文本截断和内容准备。Workspace、Knowledge 和 Agent artifact 用例在授权后有界读取，由统一的 HTTP 响应装配逻辑消费预览结果。

MinIO 原件读取执行显式字节上限，元数据大小用于提前拒绝。Office 原件的浏览器渲染与旧转换缓存退出见[浏览器 Office 预览](./2026-11-25-browser-office-preview.md)。

## 替代方案

把预览预算放进存储实现会让同一存储接口的下载与预览共享限额。各读取入口显式传入预算可以维持各自的文件大小语义。

## 后果

MinIO 原文件大小低估或缺失时，超限输入在实际读取处被拒绝。响应层使用公共文件契约，可以统一处理文本、二进制预览与下载。

## 验证

`backend/test/unit/config/test_preview_owner_boundaries.py` 检查公共文件契约与预览内容准备的静态依赖方向。`backend/test/unit/storage/test_minio_bounded_download.py` 和 `backend/test/unit/knowledge/test_file_preview.py` 验证读入上限及元数据低估的拒绝结果。真实文件和 HTTP 验证见[浏览器 Office 预览](./2026-11-25-browser-office-preview.md)。
