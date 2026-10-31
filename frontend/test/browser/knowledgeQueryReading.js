// 在真实 Vite 页面执行：playwright-cli run-code --filename=frontend/test/browser/knowledgeQueryReading.js
// 使用确定性 HTTP 夹具，不访问或修改业务文件。
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const originalViewport = page.viewportSize()
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const filename = 'Supplementary Information-' + 'very-long-document-name-'.repeat(5) + '.docx'
  const content = '# Supplementary Figures\n\n' + Array.from({ length: 24 }, (_, index) => `第 ${index + 1} 段：检索命中内容应能独立阅读，展开片段不打开文件预览。` + '文档说明。'.repeat(12)).join('\n\n')
  let results = [
    { content, score_type: 'cosine', score: 0.428, metadata: { file_id: 'reading-file', chunk_id: 'long', source: filename, start_line: 1, end_line: 8, chunk_index: 0 } },
    { content: '短片段可以直接读完。', metadata: { file_id: 'reading-file', chunk_id: 'short', source: '简短说明.md' } },
    { content: '<script>unsafe()</script>完整正文\n' + content, highlights: [[{ text: '<img src=x onerror=unsafe()>', matched: false }, { text: '命中内容', matched: true }]], metadata: { file_id: 'reading-file', chunk_id: 'highlight', source: '命中内容.md' } }
  ]
  const routeHandler = async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    let body = {}
    if (path.endsWith('/query-test')) body = results
    else if (path.endsWith('/query-params')) body = { params: { options: [] } }
    else if (path.endsWith('/basic')) body = { meta: { file_id: 'reading-file', filename, status: 'indexed', has_original_file: true, has_parsed_markdown: true } }
    else if (path.endsWith('/content')) body = { content, lines: [{ id: 'long', content, chunk_order_index: 0 }] }
    else if (path.endsWith('/download')) return route.fulfill({ contentType: 'application/octet-stream', headers: { 'content-disposition': 'attachment; filename="original.docx"' }, body: 'fixture original file' })
    else if (path === '/api/workspace/knowledge/file') return route.fulfill({ contentType: 'text/plain', body: '原文预览夹具' })
    else if (path.includes('sample-questions')) body = { questions: [] }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
  }
  await page.route('**/api/**', routeHandler)
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const { default: Query } = await import('/src/modules/knowledge/ui/QuerySection.vue')
    const { useKnowledgeBaseStore } = await import('/src/modules/knowledge/model/knowledgeBase.js')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { useUserStore } = await import('/src/modules/identity/model/user.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'knowledge-reading-fixture', isAdmin: () => true })
    const pinia = createPinia()
    useKnowledgeBaseStore(pinia).knowledgeBase = { kb_id: 'reading-kb', can_manage: false }
    const theme = useThemeStore(pinia)
    const root = document.querySelector('#app').__vue_app__
    const host = document.createElement('div')
    host.id = 'knowledge-reading-check'
    Object.assign(host.style, { position: 'fixed', inset: '24px', zIndex: 1000, padding: '16px', overflow: 'hidden', background: 'var(--gray-0)', fontFamily: 'var(--font-family)' })
    document.body.append(host)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h(Query)) })
    app.use(pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    const user = useUserStore(root.config.globalProperties.$pinia)
    const restoreAuth = () => configureApiAuth({ getToken: () => user.token, isAdmin: () => user.isAdmin, isSuperAdmin: () => user.isSuperAdmin })
    window.knowledgeReading = { app, host, theme, restoreAuth }
  })
  const host = page.locator('#knowledge-reading-check')
  const preview = host.locator('.query-document-preview')
  const first = host.locator('.result-item').first()
  const query = async () => {
    await host.locator('textarea').fill('检索阅读测试')
    await host.getByRole('button', { name: '检索知识库', exact: true }).click()
    await first.waitFor()
  }
  try {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await query()
    await first.getByRole('button', { name: '展开完整片段', exact: true }).waitFor()
    const collapsedHeight = await first.locator('.result-content').evaluate(el => {
      const height = el.getBoundingClientRect().height
      if (height > parseFloat(getComputedStyle(el).lineHeight) * 6 + 1) throw new Error('折叠区域超过6行')
      return height
    })
    await first.locator('.result-content').click()
    check(await preview.count() === 0, '点击正文误开预览')
    await first.getByRole('button', { name: '展开完整片段', exact: true }).click()
    check(await preview.count() === 0, '展开误开预览')
    check(await first.locator('.result-content').textContent() === content, '展开未展示完整片段')
    const expandedHeight = await first.locator('.result-content').evaluate(el => el.getBoundingClientRect().height)
    check(expandedHeight > collapsedHeight * 2, '长片段未默认收起')
    await first.getByRole('button', { name: '收起片段', exact: true }).click()
    check(await host.locator('.result-item').nth(1).locator('.result-expand').count() === 0, '短内容显示多余展开按钮')
    const highlighted = host.locator('.result-item').nth(2)
    await highlighted.getByRole('button', { name: '展开完整片段', exact: true }).click()
    check((await highlighted.locator('.result-content').textContent()).startsWith('<script>unsafe()</script>'), '高亮展开缺失完整正文')
    check(await highlighted.locator('script,img').count() === 0, '不可信文本被渲染为HTML')
    await highlighted.getByRole('button', { name: '收起片段', exact: true }).click()
    await first.locator('.result-open').focus()
    await page.keyboard.press('Enter')
    await preview.locator('.markdown-content').waitFor()
    await first.locator('.result-open').evaluate(el => el.blur())
    const layouts = []
    for (const mode of ['light', 'dark']) {
      await page.evaluate(mode => window.knowledgeReading.theme.setTheme(mode), mode)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        const compact = preview.getByRole('button', { name: '更多文件操作', exact: true })
        const availableWidth = await preview.locator('.file-detail-embedded').evaluate(el => el.clientWidth)
        const expectsCompact = availableWidth <= 640
        check(await compact.isVisible() === expectsCompact, '工具栏未按容器宽度收纳：' + JSON.stringify(await preview.locator('.file-detail-embedded').evaluate(el => ({ width: el.clientWidth, container: getComputedStyle(el).container, compactDisplay: getComputedStyle(el.querySelector('.compact-controls')).display, expandedDisplay: getComputedStyle(el.querySelector('.expanded-controls')).display, headerWidth: el.querySelector('.modal-title-wrapper').getBoundingClientRect().width }))))
        const layout = await preview.locator('.modal-title-wrapper').evaluate(el => {
          const name = el.querySelector('.file-name'), close = el.querySelector('.custom-close-btn')
          const a = name.getBoundingClientRect(), b = close.getBoundingClientRect()
          return { headerHeight: el.getBoundingClientRect().height, filenameEllipsis: name.scrollWidth > name.clientWidth, filenameWhiteSpace: getComputedStyle(name).whiteSpace, aligned: Math.abs((a.top + a.bottom) / 2 - (b.top + b.bottom) / 2) < 2, overflow: el.scrollWidth > el.clientWidth }
        })
        check(layout.aligned && !layout.overflow && layout.filenameEllipsis && layout.filenameWhiteSpace === 'nowrap', `标题换行或未省略：${JSON.stringify(layout)}`)
        layouts.push({ mode, width, availableWidth, ...layout })
        if (expectsCompact) {
          await compact.click()
          const hoveredItem = page.getByRole('menuitem', { name: 'Chunks', exact: true })
          await hoveredItem.hover()
          await hoveredItem.evaluate(async el => { await Promise.all(el.getAnimations().map(animation => animation.finished.catch(() => {}))) })
          const colors = await page.locator('.ant-dropdown.knowledge-file-actions:visible').evaluate(el => {
            const swatch = document.createElement('span')
            document.body.append(swatch)
            const token = name => { swatch.style.color = `var(${name})`; return getComputedStyle(swatch).color }
            const selected = getComputedStyle(el.querySelector('.ant-dropdown-menu-item-selected'))
            const hovered = getComputedStyle(el.querySelector('.ant-dropdown-menu-item-active'))
            const result = { selected: selected.backgroundColor, expectedSelected: token('--gray-100'), text: selected.color, expectedText: token('--gray-900'), hovered: hovered.backgroundColor, expectedHovered: token('--gray-50') }
            swatch.remove()
            return result
          })
          check(colors.selected === colors.expectedSelected && colors.text === colors.expectedText && colors.hovered === colors.expectedHovered, `菜单颜色未匹配主题：${JSON.stringify(colors)}`)
          await page.locator('.ant-dropdown.knowledge-file-actions:visible').screenshot({ path: `/tmp/yuxi-knowledge-menu-${mode}.png` })
          await compact.click()
        }
        await host.screenshot({ path: `/tmp/yuxi-knowledge-reading-${mode}-${width}.png` })
      }
    }
    await page.setViewportSize({ width: 1024, height: 1000 })
    const more = preview.getByRole('button', { name: '更多文件操作', exact: true })
    await more.click()
    await page.getByRole('menuitem', { name: 'Markdown 源码', exact: true }).click()
    await preview.locator('.document-source').waitFor()
    await more.click()
    await page.getByRole('menuitem', { name: 'Chunks', exact: true }).click()
    await preview.locator('.chunk-browser').waitFor()
    await more.click()
    await page.getByRole('menuitem', { name: '源文件', exact: true }).click()
    await preview.getByText('原文预览夹具', { exact: true }).waitFor()
    await more.click()
    await page.getByRole('menuitem', { name: 'Markdown 预览', exact: true }).click()
    await preview.locator('.markdown-content').waitFor()
    await more.click()
    const downloadEvent = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: '下载原文', exact: true }).click()
    const download = await downloadEvent
    let downloaded = ''
    for await (const chunk of await download.createReadStream()) downloaded += chunk.toString()
    check(downloaded === 'fixture original file', '下载原文未使用现有下载链路')
    await more.click()
    const markdownEvent = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: '下载 Markdown', exact: true }).click()
    const markdown = await markdownEvent
    check(markdown.suggestedFilename().endsWith('.md'), 'Markdown下载名称错误')
    let markdownText = ''
    for await (const chunk of await markdown.createReadStream()) markdownText += chunk.toString()
    check(markdownText === content, 'Markdown下载内容不完整')
    await preview.getByRole('button', { name: '关闭预览', exact: true }).click()
    check(await preview.count() === 0, '关闭未收起侧栏')
    await first.getByRole('button', { name: '展开完整片段', exact: true }).click()
    results = [{ content: '替换后的短片段', metadata: { file_id: 'reading-file', chunk_id: 'long', source: filename } }]
    await query()
    check(await first.locator('.result-expand').count() === 0, '新结果继承旧展开状态')
    return { collapsedHeight, expandedHeight, layouts, filenameOnlyOpensPreview: true, safeText: true, menuViews: true, downloads: true, close: true, newQueryResets: true }
  } finally {
    await page.evaluate(previousTheme => {
      window.knowledgeReading?.theme.setTheme(previousTheme || 'light')
      if (previousTheme === null) localStorage.removeItem('theme')
      window.knowledgeReading?.app.unmount(); window.knowledgeReading?.host.remove(); window.knowledgeReading?.restoreAuth(); delete window.knowledgeReading
    }, originalTheme)
    await page.setViewportSize(originalViewport)
    await page.unroute('**/api/**', routeHandler)
  }
}
