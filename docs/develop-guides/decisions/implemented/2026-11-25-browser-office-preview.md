# 浏览器 Office 预览

状态：implemented
类型：feature
Owner：frontend/src/shared/ui/OfficePreview.vue

## 问题

知识库、个人工作区和 Agent 文件预览只把 `.docx`、`.pptx` 交给 LibreOffice 转成 PDF，再走现有 pdf.js 预览。`.xls`、`.xlsx` 没有预览。继续把 Excel 接进同一条转换链路会扩大 LibreOffice 的维护面，也不能让 Word、表格和演示文稿共用一次浏览器渲染。

## 决策

### 实现方案

浏览器预览 Office 原文件。前端只安装 `@file-viewer/vue3` 与 `@file-viewer/renderer-word`、`@file-viewer/renderer-spreadsheet`、`@file-viewer/renderer-pptx`。Vite 插件按 `docx`、`xlsx`、`xls`、`pptx` 装配渲染器，`inject: false`，预览组件首次打开时再动态加载虚拟模块。Worker、WASM 和字体由插件复制到应用自己的静态产物，运行时不配置外部 CDN 地址。

预览读取默认返回这四种扩展名的原始字节，`preview_type` 为 `office`，文件名保留真实扩展名。前端用已有鉴权请求下载响应，再交给带扩展名的 `File`；不把带凭证的 URL 交给预览组件，也不为这份原文件创建对象 URL。Markdown、HTML、图片、PDF 和文本预览仍走原来的读取与展示。30 MB 预览上限不变。

`.docx`、`.pptx` 在浏览器渲染失败时，同一预览接口用 `office_render=pdf` 进入既有 LibreOffice 转换和缓存，结果仍由 `PdfPreview` 显示。`.xls`、`.xlsx` 不进入转换。LibreOffice 与 Docker 中的安装保持不动。pdf.js 继续负责 PDF，file-viewer 的 PDF 渲染器不安装。

`OfficePreview` 在卸载、KeepAlive 停用，以及宿主尺寸变为 0 时卸下渲染器。全屏与内联不同时挂载两份 Office 渲染器。预览缓存仍只拥有对象 URL；Office 原文件没有对象 URL。

Knowledge、Workspace、Viewer 和 Agent artifact 的预览用例各自保持读取与授权边界，只把 Office 分支从“默认转 PDF”改为“默认原文件，显式请求才转 PDF”。

## 替代方案

安装 `@file-viewer/preset-office` 或完整包：能少写渲染器清单，但会带上 PDF、OFD 或其他当前不需要的引擎。继续只扩展 LibreOffice：Excel 仍依赖服务端转换，不能去掉这条预览对转换进程的等待。预览接口继续返回 PDF、另用下载接口拿原文件：下载限额和响应语义与 30 MB 预览不同，调用方还要维护两套字节来源。

## 后果

打开 Office 预览会按文件类型额外下载对应渲染器 chunk，以及该渲染器的同源 Worker、WASM 或字体。这些资源不在聊天入口和知识库列表的初始脚本里。docx/pptx 的默认预览响应从 PDF 变为原文件；需要旧 PDF 结果的调用方显式传 `office_render=pdf`。转换失败、超限和缓存竞争仍由原来的 LibreOffice 路径处理。生产 Nginx 为 `.wasm` 声明 `application/wasm`。

## 验证

`frontend/test/unit/officePreview.test.js` 确认四种扩展名走 Office 原文件、PDF 与 Markdown 响应不变、回退请求带鉴权且不生成给预览器的对象 URL，并拒绝完整包、PDF 渲染器和外部 CDN 地址。`frontend/test/unit/officePreviewAssets.test.js` 确认构建产物中的 Office 资源来自同源文件。后端单测覆盖默认原文件、docx/pptx 的 PDF 回退缓存，以及 xlsx 不进入转换。删除 `.wasm` MIME 映射的负向断言在 Nginx 配置测试中。

`frontend/test/browser/officePreview.js` 在 Vite 页面挂载 Agent 文件预览、工作区预览面板和知识库文件详情，用 `backend/test/data` 的 docx、xlsx、pptx 断言可见正文，并覆盖浅色、深色、768px、加载、空预览，以及无效 docx 进入“正在使用服务端转换”后交给现有 PDF 预览。它不代替已登录的知识库、工作区、Agent 路由；那些页面仍要 Compose 和登录。

真实浏览器里的首屏内存和 CPU 采样未作为本决定的门禁；构建体积由同一变更的生产构建测量，并写在对应 PR 中。
