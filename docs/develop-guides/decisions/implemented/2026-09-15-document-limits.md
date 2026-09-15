# 知识库文档限额与任务快照

状态：implemented
类型：feature
Owner：backend/package/yuxi/services/document_limits_service.py

## 问题
管理员需要在页面调整知识库文档字节和 PDF 页数限额，同时避免覆盖其他管理员修改、越过部署能力或改变已接收任务。

## 决策
ConfigOption 保存限额和 revision，专用 repository 在行锁内比较版本并提交；超级管理员通过专用接口修改，已登录用户读取有效值。接收入口直接读取 PostgreSQL 的有效字节限额。入队固化服务端快照，worker 将顶层 payload 传给解析器，客户端 params 中的同名字段不参与信任。无快照的历史任务保持原行为。PDF 在字节检查后先执行原有结构预检，再检查页数；界面显示有效限额并将操作按钮放在字段下方。

## 替代方案
仅环境变量需要重复部署；仅前端限制可被直接 HTTP 请求绕过；解析时重新读取设置会改变已接受任务的约定。配置覆盖与任务快照复用现有配置表、任务队列和解析服务。

## 后果
限额作用于知识库文档接收及解析；个人文件、聊天附件和外部 OCR 服务能力独立。文字 PDF 同样受总页数限制。默认 100 MiB / 500 页，部署硬上限默认同值；提高硬上限需部署方协调 OCR、资源和上层代理。生产 nginx 按字节硬上限加 1 MiB multipart 余量渲染，API 与 worker 需同版本发布。配置没有添加外部 OCR 私有认证头，也不承诺第三方服务接受默认页数。批处理 Task 完成可为 success，其中超限文件以 result.failed 和文件 error_parsing 表达失败。

## 验证
真实 JWT / TCP HTTP / PostgreSQL 集成 `test_document_limits_http.py` 通过 1 项，覆盖权限、非法值、通用接口旁路、并发 409、数据库重读、接收超限及恢复默认。`test_document_limits_worker.py` 通过 1 项：实际 Tasker 持久入队与 process_task 执行、真实 MinIO 原文及解析产物重读、PG 文件终态证明旧快照保留和新限额失败；客户端伪造旧快照仍受新限额约束。该项与文档限额 parser unit 合跑 34 项通过，包含真实坏 PDF 页槽的结构诊断回归。这是同进程 assembled integration；独立 ARQ 进程及 HTTP 上传至 worker 的完整 E2E 未执行。

真实 nginx 容器执行候选启动脚本：硬上限 1 MiB 渲染请求上限 2M，nginx -t 通过，$uri / $host 保留；1 MiB 文件的 multipart 请求通过，2 MiB + 1 字节请求体返回 413，0、负值、非整数和非数字硬上限使启动失败。此验证使用隔离 echo upstream，没有构建完整 Web 镜像。

组件运行时测试覆盖非法输入、部署上限、版本冲突和恢复默认。浏览器加载 `web/test/browser/documentLimits.html` 的实际组件与合成设置 API，验证冲突后保留草稿、重试和重置，并保留截图；该浏览器 fixture 不连接生产，也不证明真实登录后的 HTTP 集成。公开发布、合并和生产部署状态独立于以上实现验证。
