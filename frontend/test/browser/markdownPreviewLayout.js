// 在开发页面运行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/markdownPreviewLayout.js
// 使用真实组件与浏览器样式，不读取或修改业务数据。
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const originalViewport = page.viewportSize()
  const errors = []
  const onError = error => errors.push(error.message)
  page.on('pageerror', onError)
  try {
    await page.evaluate(async () => {
      const module = await (await fetch('/src/shared/model/theme.js')).text()
      const dependency = name => module.match(new RegExp(`from "([^"]*/${name}\\.js[^"]*)"`))[1]
      const { createApp, h, reactive } = await import(dependency('vue'))
      const { createPinia } = await import(dependency('pinia'))
      const { default: AgentFilePreview } = await import('/src/modules/session/ui/workspace/AgentFilePreview.vue')
      const { default: MarkdownPreview } = await import('/src/modules/workspace/ui/MarkdownPreview.vue')
      const { default: DocumentPreview } = await import('/src/modules/workspace/ui/MarkdownDocumentPreview.vue')
      const { default: ChunksModal } = await import('/src/modules/knowledge/ui/KbFileChunksModal.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const antUrl = module.match(/from "([^"]*ant-design-vue\.js[^"]*)"/)[1]
      const { default: AntDesign } = await import(antUrl)
      const content = '| 项目 | 状态 |\n| --- | --- |\n| 示例 | 完成 |\n\n# Markdown 文档\n\n正文包含 `inline`、[链接](https://example.com) 和长文本。\n\n```python\nprint(42)\n```\n\n```html:preview\n<html><body><p>内嵌预览</p></body></html>\n```'
      const references = ['one', 'two'].map(id => ({
        answer_start_line: 3, answer_end_line: 3, quote: '正文引用',
        source: { id, kind: 'web', title: `来源 ${id}`, url: `https://${id}.example.com` }
      }))
      const state = reactive({ content, references, chunksOpen: false, clicks: 0 })
      const host = document.createElement('div')
      host.id = 'markdown-layout-probe'
      host.style.cssText = 'position:fixed;inset:0;z-index:999;background:var(--gray-0);color:var(--gray-900);padding:16px;overflow:auto'
      document.body.append(host)
      const pinia = createPinia()
      const app = createApp({ render: () => h('div', [
        h(AgentFilePreview, { filePath: '/README.md', file: { content: state.content, previewType: 'markdown' } }),
        h(MarkdownPreview, { id: 'compact-markdown', class: 'caller-markdown', compact: true, codeCopy: true, content: state.content, references: state.references, onClick: () => state.clicks++ }),
        h(DocumentPreview, { content: '# 文档容器\n\n正文', startLine: 3, endLine: 3 }),
        h(ChunksModal, { open: state.chunksOpen, fileGroup: { filename: '片段.md', chunks: [{ content: '# 检索片段\n\n第一段\n\n第二段' }] }, 'onUpdate:open': value => { state.chunksOpen = value } })
      ]) })
      app.use(pinia)
      app.use(AntDesign)
      app.mount(host)
      window.markdownLayoutProbe = { app, host, state, theme: useThemeStore(pinia) }
    })
    const preview = page.locator('#markdown-layout-probe .agent-file-preview .yk-markdown-preview')
    await preview.locator('h1').waitFor()
    /** Markdown 表格继承文档底色，不能套用管理表格表面。 */
    const checkTransparentTable = async () => {
      const colors = await preview.locator('table, th, td, tr').evaluateAll(elements => elements.map(element => getComputedStyle(element).backgroundColor))
      check(colors.length > 3 && colors.every(color => color === 'rgba(0, 0, 0, 0)'), 'Markdown 表格不应带独立背景')
    }
    await checkTransparentTable()
    const opaqueRows = await page.addStyleTag({ content: '.yk-markdown-preview tr {background:#222!important}' })
    let opaqueRowsRejected = false
    try { await checkTransparentTable() } catch (error) { opaqueRowsRejected = error.message.includes('Markdown 表格不应带独立背景') }
    finally { await opaqueRows.evaluate(element => element.remove()) }
    check(opaqueRowsRejected, '未拒绝 Markdown 表格不透明背景')
    const measurements = []
    for (const dark of [false, true]) {
      await page.evaluate(value => window.markdownLayoutProbe.theme.setTheme(value), dark)
      for (const width of [1440, 1024, 768, 375]) {
        await page.setViewportSize({ width, height: 900 })
        const measured = await preview.evaluate(el => {
          const style = getComputedStyle(el)
          const scope = el.parentElement.getAttributeNames().find(name => name.startsWith('data-v-'))
          const pagePadding = parseFloat(style.getPropertyValue('--page-padding'))
          const rect = el.getBoundingClientRect()
          const title = el.querySelector('h1').getBoundingClientRect()
          return { scopeInherited: el.hasAttribute(scope), top: parseFloat(style.paddingTop), left: parseFloat(style.paddingLeft), expectedLeft: pagePadding - 4, titleInset: title.left - rect.left, fontSize: parseFloat(style.fontSize), expectedFontSize: parseFloat(getComputedStyle(document.documentElement).fontSize) * 0.85 }
        })
        check(measured.scopeInherited, '公共 Markdown 根节点丢失调用方 scoped 标记')
        check(measured.top === 16 && measured.left === measured.expectedLeft, `文件预览间距失效：${JSON.stringify(measured)}`)
        check(Math.abs(measured.titleInset - measured.expectedLeft) < 1, '标题仍贴边或出现重复留白')
        check(Math.abs(measured.fontSize - measured.expectedFontSize) < 0.1, '文件预览字号规则失效')
        const compact = await page.locator('#compact-markdown').evaluate(el => ({ padding: getComputedStyle(el).padding, size: getComputedStyle(el).fontSize, classForwarded: el.classList.contains('caller-markdown') }))
        check(compact.padding === '0px' && compact.size === '14px' && compact.classForwarded, '紧凑预览或调用方属性发生回归')
        const document = await page.locator('#markdown-layout-probe .document-text-preview').evaluate(el => ({ padding: getComputedStyle(el).padding, childPadding: getComputedStyle(el.querySelector('.yk-markdown-preview')).padding }))
        check(document.padding === '16px' && document.childPadding === '0px', '文档容器重复留白')
        await checkTransparentTable()
        measurements.push({ dark, width, ...measured })
      }
      await page.screenshot({ path: `/tmp/yuxi-markdown-padding-${dark ? 'dark' : 'light'}-mobile.png` })
      await page.setViewportSize({ width: 1440, height: 900 })
      await page.locator('#markdown-layout-probe .agent-file-preview').screenshot({ path: `/tmp/yuxi-markdown-padding-${dark ? 'dark' : 'light'}.png` })
    }
    await page.evaluate(() => { window.markdownLayoutProbe.state.chunksOpen = true })
    const chunk = page.locator('.chunk-markdown')
    await chunk.locator('h1').waitFor()
    check(await chunk.locator('h1').evaluate(el => getComputedStyle(el).marginTop === '0px'), '知识库片段标题的 scoped 排版失效')
    check(await chunk.locator('p').first().evaluate(el => getComputedStyle(el).marginBottom === '8px'), '知识库片段段落间距失效')
    await page.getByRole('button', { name: '关闭检索片段弹窗' }).click()
    const capsule = page.locator('#compact-markdown .reference-citation')
    const card = page.getByRole('dialog', { name: '来源预览', exact: true })
    await capsule.click()
    await card.waitFor()
    await card.getByRole('button', { name: '下一个来源', exact: true }).click()
    check(await card.getByText('2/2', { exact: true }).isVisible(), '来源浮层分页失效')
    await page.evaluate(() => { window.markdownLayoutProbe.state.content += '\n\n新增正文。' })
    await page.locator('#compact-markdown').getByText('新增正文。', { exact: true }).waitFor()
    await card.waitFor({ state: 'detached' })
    await capsule.focus()
    await page.keyboard.press('ArrowDown')
    await card.waitFor()
    await page.keyboard.press('Escape')
    await card.waitFor({ state: 'detached' })
    check(await capsule.evaluate(el => el === document.activeElement), '更新正文后浮层键盘焦点恢复失效')
    check(await page.evaluate(() => window.markdownLayoutProbe.state.clicks > 0), '调用方点击事件未转发')
    check(await page.locator('#markdown-layout-probe .source-line-highlight').count() > 0, '原文行高亮失效')
    check(await page.locator('#compact-markdown iframe').count() === 0, '旧HTML围栏不能创建iframe')
    await page.evaluate(() => { window.markdownLayoutProbe.state.content += '\n\n后续正文。' })
    await page.locator('#compact-markdown').getByText('后续正文。', { exact: true }).waitFor()
    check(await page.locator('#compact-markdown iframe').count() === 0, '更新正文仍不能创建旧HTML预览')
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.locator('#compact-markdown .markdown-code-copy-btn').first().click()
    check(await page.evaluate(() => navigator.clipboard.readText()) === 'print(42)\n', '代码复制内容错误')
    check(errors.length === 0, `浏览器运行异常：${errors.join('; ')}`)
    return { opaqueRowsRejected, measurements, chunks: true, referenceUpdates: true, codeCopy: true, legacyHtmlAsCode: true }
  } finally {
    await page.evaluate(theme => {
      const probe = window.markdownLayoutProbe
      if (probe) { probe.theme.setTheme(theme === 'dark'); probe.app.unmount(); probe.host.remove(); delete window.markdownLayoutProbe }
      if (theme === null) localStorage.removeItem('theme')
      else localStorage.setItem('theme', theme)
    }, originalTheme)
    await page.setViewportSize(originalViewport)
    page.off('pageerror', onError)
  }
}
