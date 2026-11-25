# 浏览器 Office 预览

状态：implemented
类型：feature
Owner：frontend/src/shared/ui/OfficePreview.vue

## 问题

知识库、个人工作区和 Agent 文件预览依靠 LibreOffice 把 DOCX、PPTX 转成 PDF。XLS 和 XLSX 没有预览。浏览器解析四种格式可以补齐 Excel，并省去 Word、PowerPoint 首次预览的转换等待。

## 决策

产品预览默认返回 DOCX、PPTX、XLS、XLSX 原件，保留 30 MB 预览上限。响应用允许的扩展名确定媒体类型，标记为附件并禁止 MIME 嗅探。前端通过已鉴权的请求读取响应，把保留文件名的 `File` 交给浏览器渲染器。Public v1 产物预览保留 DOCX、PPTX 默认转 PDF 的协议。产品端通过内部路由复用产物读取、Thread 授权与文件边界。

Word 和 PowerPoint 浏览器渲染失败时，原件入口用同一个读取函数请求 `office_render=pdf`。知识库、Workspace、Viewer 和 Agent 产物沿用各自的转换及缓存职责，结果由现有 pdf.js 组件显示。Excel 直接报告渲染结果。全屏移动当前 Office 渲染器，停用或离开预览时释放实例。

Vite 只装配 Word、表格和演示文稿渲染器，首次打开 Office 文件时再加载。开发静态 Worker 放在依赖缓存，生产构建由插件写入同源产物。开发态的 JSZip ESM 适配沿已声明的 Word 渲染器解析依赖。

Office 正文沿用应用的预览标题栏。文件操作和主题由应用管理，表格通过工作表标签导航，PPTX 通过滚动和应用全屏阅读。锁定版 PPTX 渲染器由 pnpm 补丁移除内置放映按钮与键盘监听。上游提供关闭放映的选项时，可用该选项替换补丁。

## 替代方案

全量预设会同时引入 PDF、OFD 等无消费者的渲染器。继续让 LibreOffice 处理所有预览会保留服务端转换等待。移除 Word 和 PowerPoint 的转换可以缩减镜像，但会使浏览器无法处理的文档失去现有回退。产品的下载入口有不同的大小预算和响应语义，预览使用现有读取边界。

## 后果

首次打开 Office 文件需要加载对应渲染器和 Worker。LibreOffice 仍服务于 Word、PowerPoint 的 PDF 回退以及 Public v1 预览。Office 文件的复杂版式保真度取决于浏览器渲染器与原转换能力。

## 验证

`frontend/test/unit/officePreview.test.js` 核对原件、PDF 和 Markdown 的浏览器响应归一。`frontend/test/unit/officePreviewAssets.test.js` 检查构建资源来自同源，且保持按需加载。

`backend/test/integration/api/test_workspace_runtime_cache_api.py` 回读 Office 响应头、原件、PDF 和运行目录缓存。`backend/test/integration/api/test_chat_router.py` 回读 Public PDF、产品原件及 PDF 回退，并验证跨用户访问被拒绝。这两条契约进入系统测试 HTTP 批次。

`frontend/test/browser/officePreview.js` 从已登录的本地测试页面验证知识库、个人工作区和 Agent，使用四种真实 Office 夹具检查可见正文。阻断浏览器渲染模块时，探针回读真实 LibreOffice PDF 和浏览器画布。
