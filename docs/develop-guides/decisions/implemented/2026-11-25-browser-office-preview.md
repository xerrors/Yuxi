# 浏览器 Office 预览

状态：implemented
类型：feature
Owner：frontend/src/shared/ui/OfficePreview.vue

## 问题

知识库、个人工作区和 Agent 文件预览依靠 LibreOffice 把 DOCX、PPTX 转成 PDF。XLS 和 XLSX 没有预览。浏览器解析四种格式可以补齐 Excel，并省去 Word、PowerPoint 首次预览的转换等待。

## 决策

### 实现方案

产品预览返回 DOCX、PPTX、XLS、XLSX 原件，保留 30 MB 预览上限。响应用允许的扩展名确定媒体类型，标记为附件并禁止 MIME 嗅探。前端通过已鉴权的请求读取响应，把保留文件名的 `File` 交给浏览器渲染器。产品与 Public v1 共用产物读取端点及 Thread 授权。公开接口的 `preview=true` 对 DOCX、PPTX、XLS、XLSX 返回原件和对应的 Office MIME，客户端可按响应头选择渲染或保存文件。

共享预览标题栏和渲染失败提示中的下载按钮复用已读取的 Office 原件。知识库详情的下载菜单继续请求原件下载接口。Workspace、Knowledge、Viewer 和产物预览共用预览内容准备逻辑，移除 PDF 转换、转换缓存及 API 镜像的 LibreOffice 依赖。原生 PDF 继续由现有 pdf.js 组件显示。全屏移动当前 Office 渲染器，停用或离开预览时释放实例。

Vite 只装配 Word、表格和演示文稿渲染器，首次打开 Office 文件时再加载。开发静态 Worker 放在依赖缓存，生产构建由插件写入同源产物。开发态的 JSZip ESM 适配沿已声明的 Word 渲染器解析依赖。

Office 正文沿用应用的预览标题栏。文件操作和主题由应用管理，表格通过工作表标签导航，PPTX 通过滚动和应用全屏阅读。锁定版 PPTX 渲染器由 pnpm 补丁移除内置放映按钮与键盘监听。上游提供关闭放映的选项时，可用该选项替换补丁。

锁定版表格渲染器的 pnpm 补丁将初始解析提示简化为一句话和一个转圈。表格可继续滚动时，`e-virt-table` 的默认滚动处理会拦截带修饰键的滚轮事件。补丁将 Ctrl 或 Cmd 加滚轮留给 FileViewer 缩放，普通滚轮继续滚动表格。上游分别支持简洁的解析状态和组合键缩放后，通过浏览器回归即可移除对应补丁。

共用契约见[文件预览与原文件读取边界](./2026-09-30-file-preview-boundaries.md)。历史对象回收见[知识处理职责](./2026-10-30-knowledge-processing-ownership.md)，当前产物的刷新规则见[预览更新](./2026-10-18-demo-search-and-preview-recovery.md)。

## 替代方案

全量预设会同时引入 PDF、OFD 等无消费者的渲染器。继续让 LibreOffice 处理所有预览会保留服务端转换等待。浏览器渲染失败时再转换 PDF，会为一次文件查看增加服务端计算和持久缓存。直接下载原件让用户可以用本机应用打开复杂文件。产品的下载入口有不同的大小预算和响应语义，预览使用现有读取边界。

## 后果

首次打开 Office 文件需要加载对应渲染器和 Worker。过去按 PDF 读取 DOCX、PPTX 预览的 Public v1 客户端需按 Office 原件处理响应。旧版知识库生成的派生 PDF 由文档删除流程清理。旧运行目录中的 `cache/office-previews` 已停止读写，升级后可按需清理。Office 文件的复杂版式保真度取决于浏览器渲染器。

## 验证

`frontend/test/unit/officePreview.test.js` 核对原件、PDF 和 Markdown 的浏览器响应归一。`frontend/test/unit/officePreviewAssets.test.js` 检查构建资源来自同源，且保持按需加载。

`backend/test/integration/api/test_workspace_preview_api.py` 回读 Office 响应头及预览和下载原件。`backend/test/integration/api/test_chat_router.py` 回读 Public v1 Office 原件和响应头，并验证跨用户访问被拒绝。这两条契约进入系统测试 HTTP 批次。

`frontend/test/browser/officePreview.js` 从已登录的本地测试页面验证知识库、个人工作区和 Agent，使用四种真实 Office 夹具检查可见正文。延迟表格解析器可验证解析提示和转圈的数量，在可滚动的 XLSX 表格中验证 Ctrl 加滚轮可放大并可缩到初始比例以下。阻断 Office 渲染模块时，探针触发浏览器下载，并验证原生 PDF 仍能绘制画布。
