# PDF 预览资源加载边界

状态：implemented
类型：bug-fix
Owner：web/src/components/common/PdfPreview.vue

## 问题

PDF 预览偶发提示「无法加载 PDF 文件或文件格式受损」，但文件本身有效。生产 Nginx 基础镜像的 mime.types 未映射 `.mjs`，pdf.js 模块 Worker 以 `application/octet-stream` 下发而被浏览器拒绝执行；组件把取消、Worker 失败与格式错误统一显示为同一文案；CMap 依赖 cdn.jsdelivr.net，受限网络下中文 PDF 无法补齐字体映射；快速切换预览文件时，过期加载的异常还会覆盖新加载的状态。

## 决策

生产 Nginx 在 http 层为 `.mjs` 显式声明 `application/javascript`；同层 types 块与 mime.types 的合并行为已在真实 nginx:alpine 容器中验证。`PdfPreview` 引入加载代次，仅当前代次可写入 loading、error 与文档状态，过期加载销毁任务后静默退出；错误文案按 pdf.js 异常类型区分渲染组件失败、网络失败、加密与格式损坏。pdf.js CMap 资源随 Web 构建发布到 `/assets/pdfjs-cmaps/`，开发态由 Vite 中间件从依赖目录直读，运行时不再访问外部 CDN。

## 替代方案

仅修复 Nginx MIME：不解决取消竞态与 CDN 依赖，开发环境问题依旧。回退 pdf.js 主线程 fake worker：模块脚本同样受 JavaScript MIME 约束，不能绕过。引入 vite-plugin-static-copy 复制 CMap：为等价能力新增依赖，改为配置内插件。

## 后果

Web 静态产物增加约 1.6 MB CMap 资源；`cMapUrl` 指向同源路径，升级 pdfjs-dist 时 CMap 随 lockfile 同步更新。Nginx 变更需要重建并重新部署 web 镜像才进入生产。

## 验证

`web/test/unit/pdfPreviewErrors.test.js` 覆盖错误映射，Worker 失败不得再显示格式受损。`web/test/unit/pdfPreviewAssets.test.js` 与 `backend/test/unit/config/test_nginx_static_assets.py` 作为负向控制：删除本地 CMap、恢复 CDN 地址或移除 `.mjs` 映射都会使对应断言失败。`.mjs` MIME 行为在真实 nginx:alpine 容器中以 curl 验证。真实浏览器下多页文档渲染与快速切换场景待人工复核。
