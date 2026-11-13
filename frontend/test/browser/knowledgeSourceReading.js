// 在真实 Vite 页面执行；HTTP 夹具验证来源交互，资源权限另由后端 integration 验证。
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const viewport = page.viewportSize()
  const previousTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const requests = []
  let mode = 'loading'
  let releaseRead
  const localReads = []
  const handler = async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    if (path.startsWith('/api/knowledge/knowledge-bases/kb-A/documents/same-file/')) {
      localReads.push(path)
      if (path.endsWith('/basic')) return route.fulfill({ json: { meta: { file_id: 'same-file', filename: '同名手册.md', status: 'indexed', has_parsed_markdown: true, has_original_file: true } } })
      if (path.endsWith('/content')) return route.fulfill({ json: { content: Array.from({ length: 45 }, (_, index) => `本地原文第 ${index + 1} 行`).join('\n'), lines: [] } })
    }
    if (path !== '/api/v1/knowledge/tools/open_kb_document') return route.fulfill({ status: 403, json: { detail: '不应调用管理接口' } })
    const payload = route.request().postDataJSON()
    requests.push(payload)
    if (mode === 'loading') await new Promise(resolve => { releaseRead = resolve })
    if (mode === 'denied') return route.fulfill({ status: 404, json: { detail: '当前无权读取原文' } })
    const start = payload.offset + 1
    return route.fulfill({ json: {
      kb_id: payload.kb_id, file_id: payload.file_id, offset: payload.offset, window_size: 200,
      start_line: start, end_line: start + 2, total_lines: 200,
      has_more_before: false, has_more_after: false, next_offset: null,
      content: [start, start + 1, start + 2].map(line => `${line}\t${payload.kb_id} 原文第 ${line} 行`).join('\n')
    } })
  }
  await page.route('**/api/**', handler)
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const { default: Sources } = await import('/src/modules/session/ui/workspace/TurnSourcesPanel.vue')
    const { useTurnReferencesStore } = await import('/src/modules/session/model/turnReferences.js')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    const { useUserStore } = await import('/src/modules/identity/model/user.js')
    configureApiAuth({ getToken: () => 'source-reading-fixture', isAdmin: () => false })
    const pinia = createPinia()
    useUserStore(pinia).userRole = 'user'
    const entry = useTurnReferencesStore(pinia).getEntry('source-thread', 'source-turn')
    entry.loaded = true
    entry.available = true
    entry.sources = [
      { kind: 'knowledge', kb_id: 'kb-A', file_id: 'same-file', title: '同名手册.md', content: 'A 库命中片段', supports_documents: true, supports_document_read: true, start_line: 40, end_line: 41 },
      { kind: 'knowledge', kb_id: 'kb-B', file_id: 'same-file', title: '同名手册.md', content: 'B 库命中片段', supports_document_read: true, start_line: 80, end_line: 81 },
      { kind: 'knowledge', kb_id: 'kb-readonly', file_id: 'remote', title: '仅检索来源.md', content: '提供方只返回检索片段', supports_document_read: false },
      { kind: 'knowledge', kb_id: 'kb-missing', file_id: '', title: '缺少文件身份.md', content: '仍可查看片段', supports_document_read: true },
      { kind: 'knowledge', kb_id: 'kb-legacy', file_id: 'legacy', title: '历史无行号.md', content: '历史片段', supports_document_read: true }
    ]
    const root = document.querySelector('#app').__vue_app__
    const theme = useThemeStore(pinia)
    const host = document.createElement('div')
    host.id = 'source-reading-check'
    Object.assign(host.style, { position: 'fixed', inset: '24px', zIndex: 1000, background: 'var(--gray-0)', padding: '20px' })
    document.body.append(host)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h(Sources, { threadId: 'source-thread', turnId: 'source-turn', active: true })) })
    app.use(pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    const user = useUserStore(root.config.globalProperties.$pinia)
    window.sourceReading = { app, host, theme, setAdmin: () => { useUserStore(pinia).userRole = 'admin'; configureApiAuth({ isAdmin: () => true }) }, restore: () => configureApiAuth({ getToken: () => user.token, isAdmin: () => user.isAdmin, isSuperAdmin: () => user.isSuperAdmin }) }
  })
  const host = page.locator('#source-reading-check')
  const reader = page.locator('.external-document-reader:visible')
  const chunks = page.locator('.kb-file-chunks-modal:visible')
  try {
    check(await host.getByRole('button', { name: '查看完整文件' }).count() === 3, '原文操作未按能力和文件身份显示')
    await host.getByRole('button', { name: '查看 同名手册.md 的检索片段' }).nth(0).click()
    await chunks.getByText('A 库命中片段', { exact: true }).waitFor()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => window.sourceReading.theme.setTheme(theme), theme)
      await page.setViewportSize({ width: 768, height: 900 })
      await chunks.locator('.ant-modal-content').screenshot({ path: `/tmp/yuxi-source-chunk-card-${theme}.png` })
    }
    await chunks.getByRole('button', { name: '查看原文', exact: true }).focus()
    await page.keyboard.press('Enter')
    await reader.getByRole('status').waitFor()
    await reader.locator('xpath=ancestor::div[contains(@class,"ant-modal-content")]').screenshot({ path: '/tmp/yuxi-source-reading-loading.png' })
    check(requests.at(-1).kb_id === 'kb-A' && requests.at(-1).file_id === 'same-file' && requests.at(-1).offset === 39, 'A 库来源或行号错误')
    mode = 'content'
    releaseRead()
    await reader.locator('.reader-line-highlight').first().waitFor()
    check(await reader.locator('.reader-line-highlight').count() === 2, '片段原文行号没有高亮')
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => window.sourceReading.theme.setTheme(theme), theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 900 })
        const modal = reader.locator('xpath=ancestor::div[contains(@class,"ant-modal-content")]')
        check(await modal.evaluate(el => el.scrollWidth <= el.clientWidth + 1), '原文弹窗横向溢出')
        await modal.screenshot({ path: `/tmp/yuxi-source-reading-${theme}-${width}.png` })
      }
    }
    await reader.getByRole('button', { name: '关闭文档' }).click()
    await reader.waitFor({ state: 'hidden' })
    mode = 'denied'
    await host.getByRole('button', { name: '查看 同名手册.md 的检索片段' }).nth(1).click()
    await chunks.getByText('B 库命中片段', { exact: true }).waitFor()
    await chunks.getByRole('button', { name: '查看原文', exact: true }).click()
    await reader.getByRole('alert').waitFor()
    check(requests.at(-1).kb_id === 'kb-B' && requests.at(-1).offset === 79, '同名文件串用了 A 库身份')
    mode = 'content'
    await reader.getByRole('button', { name: '重新读取', exact: true }).click()
    await reader.getByText('kb-B 原文第 80 行', { exact: true }).waitFor()
    await reader.getByRole('button', { name: '关闭文档' }).click()
    await reader.waitFor({ state: 'hidden' })
    for (const name of ['仅检索来源.md', '缺少文件身份.md']) {
      const count = requests.length
      await host.getByRole('button', { name: `查看 ${name} 的检索片段` }).click()
      await chunks.getByRole('button', { name: '关闭检索片段弹窗' }).waitFor()
      check(await chunks.getByRole('button', { name: '查看原文', exact: true }).count() === 0, '不支持原文的来源出现读取入口')
      await chunks.getByRole('button', { name: '关闭检索片段弹窗' }).click()
      check(requests.length === count, '片段预览错误发起了原文请求')
    }
    await host.getByRole('button', { name: '查看 历史无行号.md 的检索片段' }).click()
    await chunks.getByRole('button', { name: '查看原文', exact: true }).click()
    await reader.getByText('kb-legacy 原文第 1 行', { exact: true }).waitFor()
    check(requests.at(-1).offset === 0 && await reader.locator('.reader-line-highlight').count() === 0, '无行号来源伪造了定位')
    await reader.getByRole('button', { name: '关闭文档' }).click()
    await reader.waitFor({ state: 'hidden' })
    check(localReads.length === 0, '普通用户误用了管理侧文件内容接口')
    await page.evaluate(() => window.sourceReading.setAdmin())
    await host.getByRole('button', { name: '查看 同名手册.md 的检索片段' }).nth(0).click()
    await chunks.getByRole('button', { name: '查看原文', exact: true }).click()
    await page.locator('.ant-modal-content:visible .markdown-content').waitFor()
    check(localReads.some(path => path.endsWith('/basic')) && localReads.some(path => path.endsWith('/content')), '本地管理员未沿用完整文件详情')
    await page.locator('.ant-modal-content:visible').last().screenshot({ path: '/tmp/yuxi-source-local-file-detail.png' })
    await page.getByRole('button', { name: '关闭预览', exact: true }).click()
    return { requests: requests.map(({ kb_id, file_id, offset }) => ({ kb_id, file_id, offset })), sameNameIsolation: true, capabilityGating: true, lineHighlight: true, errorRetry: true, legacyReading: true, localAdminDetail: true }
  } finally {
    if (releaseRead) releaseRead()
    await page.evaluate(previousTheme => {
      window.sourceReading.theme.setTheme(previousTheme || 'light')
      if (previousTheme === null) localStorage.removeItem('theme')
      window.sourceReading.app.unmount()
      window.sourceReading.host.remove()
      window.sourceReading.restore()
      delete window.sourceReading
    }, previousTheme)
    await page.setViewportSize(viewport)
    await page.unroute('**/api/**', handler)
  }
}
