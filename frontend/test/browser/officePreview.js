// 在已登录的本地测试实例运行，脚本创建后清理自己的文件和知识库。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/officePreview.js
async (page) => {
  const check = (value, message) => {
    if (!value) throw new Error(message)
  }
  const origin = page.url().split('/').slice(0, 3).join('/')
  check(/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin), '需要本地测试实例')
  check(await page.evaluate(() => localStorage.getItem('user_token') !== null), '请先登录本地测试实例')
  const before = await page.evaluate(() => ({ theme: localStorage.getItem('theme'), width: innerWidth, height: innerHeight }))
  const suffix = await page.evaluate(() => crypto.randomUUID().slice(0, 8))
  const screenshotDir = '/tmp/yuxi-office-preview-' + suffix
  const screenshot = (name, target = page) => target.screenshot({ path: screenshotDir + '/' + name + '.png' })
  const api = (path, options = {}) => page.evaluate(async ({ path, method, data, file, fields }) => {
    const headers = { Authorization: 'Bearer ' + localStorage.getItem('user_token') }
    let body
    if (file) {
      body = new FormData()
      body.append(file.field, document.querySelector('#office-fixtures').files[file.index], file.name)
      for (const [key, value] of Object.entries(fields)) body.append(key, value)
    } else if (data) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(data)
    }
    if (path === '/api/v1/agents/sessions') headers['Idempotency-Key'] = crypto.randomUUID()
    const response = await fetch(path, { headers, method, body })
    if (!response.ok) throw new Error(method + ' ' + path + ': ' + response.status)
    return response.json()
  }, { path, method: 'GET', ...options })
  const open = (path) => page.goto(origin + path)
  const assertNoToolbar = () => page.locator('.office-preview-frame [part="toolbar"]').waitFor({ state: 'detached' })
  const watchSheetPaint = () => page.evaluate(() => {
    const fillText = CanvasRenderingContext2D.prototype.fillText
    const cells = new Set()
    window.officePaintedCells = cells
    CanvasRenderingContext2D.prototype.fillText = function (text, ...args) {
      fillText.call(this, text, ...args)
      if (this.canvas.getRootNode().host?.matches('.office-preview-frame')) cells.add(text)
    }
  })
  const query = (params) => Object.entries(params)
    .map(([key, value]) => key + '=' + encodeURIComponent(value)).join('&')
  const previewText = (text) => page.locator('.office-preview').getByText(text, { exact: false }).first()
  const scrollPdf = async (selector) => {
    await page.locator(selector).hover()
    await page.mouse.wheel(0, 1000)
    await page.waitForFunction((selector) => {
      const pane = document.querySelector(selector)
      const nextPage = pane?.querySelectorAll('.pdf-page-card')[1]
      return pane?.scrollTop > 0 && nextPage?.getBoundingClientRect().top < pane.getBoundingClientRect().bottom
    }, selector)
  }
  const localFiles = []
  const previews = []
  let kbId
  let threadId
  let projectFilePath

  const onResponse = (response) => {
    if (response.headers()['x-yuxi-preview-type'] === 'office') previews.push(response)
  }
  page.on('response', onResponse)
  const viewerModule = /file-viewer-renderers/

  try {
    const fixtures = ['测试文档.docx', 'xls-cell-preservation.xls', '测试表格.xlsx', '测试演示.pptx']
    await page.evaluate(() => {
      const input = document.createElement('input')
      input.type = 'file'
      input.id = 'office-fixtures'
      input.multiple = true
      input.hidden = true
      document.body.append(input)
    })
    await page.locator('#office-fixtures').setInputFiles(fixtures.map((name) => 'backend/test/data/' + name))
    for (const index of [0, 1]) {
      const name = suffix + '-' + fixtures[index]
      await api('/api/workspace/upload', { method: 'POST', file: { field: 'files', index, name }, fields: { parent_path: '/' } })
      localFiles.push(name)
    }

    const kb = await api('/api/knowledge/knowledge-bases', {
      method: 'POST',
      data: {
        name: `Office 预览验证 ${suffix}`,
        description: '临时浏览器测试',
        kb_type: 'milvus',
        embedding_model_spec: 'siliconflow-cn:Pro/BAAI/bge-m3'
      }
    })
    kbId = kb.kb_id
    const { file_path, content_hash } = await api(`/api/knowledge/files/upload?kb_id=${kbId}`, {
      method: 'POST', file: { field: 'file', index: 2, name: fixtures[2] }, fields: {}
    })
    await api(`/api/knowledge/knowledge-bases/${kbId}/documents/add`, {
      method: 'POST',
      data: { items: [file_path], params: { content_hashes: { [file_path]: content_hash } } }
    })

    const created = await api('/api/v1/agents/sessions', {
      method: 'POST',
      data: { agent_id: 'default-chatbot', title: `Office 预览验证 ${suffix}` }
    })
    threadId = created.id
    const uploaded = await api('/api/viewer/filesystem/upload', {
      method: 'POST',
      file: { field: 'files', index: 3, name: fixtures[3] },
      fields: { thread_id: threadId, parent_path: '/' }
    })
    projectFilePath = uploaded.entries[0].path
    await page.locator('#office-fixtures').evaluate((input) => input.remove())

    await page.setViewportSize({ width: 1280, height: 900 })
    await open('/agent')
    await page.getByRole('link', { name: '个人空间', exact: true }).click()
    await page.getByText(localFiles[0], { exact: true }).click()
    await previewText('述职报告').waitFor({ timeout: 45000 })
    await screenshot('workspace-docx')

    const viewer = await page.locator('.office-preview-host').elementHandle()
    await page.getByTitle('全屏预览', { exact: true }).click()
    await page.locator('.fullscreen-preview-overlay .office-preview').getByText('述职报告').first().waitFor()
    check(await viewer.evaluate((node) => node.closest('.fullscreen-preview-overlay') !== null), '全屏重新创建了预览器')
    await assertNoToolbar()
    await screenshot('workspace-docx-fullscreen')
    await page.locator('.fullscreen-preview-overlay').getByTitle('关闭', { exact: true }).click()
    await previewText('述职报告').waitFor()
    await page.getByTitle('全屏预览', { exact: true }).click()
    await page.goBack()
    await page.locator('.fullscreen-preview-overlay').waitFor({ state: 'hidden' })
    check(await page.evaluate(() => document.body.style.overflow !== 'hidden'), '退出页面后滚动仍被全屏锁定')

    await page.setViewportSize({ width: 768, height: 900 })
    await open('/workspace')
    await watchSheetPaint()
    await page.getByRole('row', { name: new RegExp(localFiles[1]) }).press('Enter')
    const sheets = page.locator('.office-preview')
    await sheets.getByRole('button', { name: '单行', exact: true }).waitFor({ timeout: 45000 })
    await page.waitForFunction(() => window.officePaintedCells.has('53'))
    await sheets.getByRole('button', { name: '文本与空白', exact: true }).click()
    await page.waitForFunction(() => window.officePaintedCells.has('00456'))
    await sheets.getByText('共 3 行，3 列', { exact: true }).waitFor()
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '预览导致整页横向滚动')
    await assertNoToolbar()
    await screenshot('workspace-xls-768', page.locator('.workspace-file-preview-modal .ant-modal'))

    await page.setViewportSize({ width: 1280, height: 900 })
    await open(`/extensions/knowledge-bases/${kbId}`)
    await page.getByText(fixtures[2], { exact: true }).click()
    await page.locator('.office-preview').getByRole('button', { name: '个人所得税计算', exact: true }).waitFor({ timeout: 45000 })
    await previewText('李果果').waitFor({ state: 'attached' })
    await screenshot('knowledge-xlsx', page.locator('.ant-modal:visible').filter({ has: page.locator('.office-preview') }))

    await page.evaluate(() => localStorage.setItem('theme', 'dark'))
    await open(`/agent/${threadId}`)
    const openPanel = page.getByRole('button', { name: '打开侧边栏' })
    const threadTab = page.getByRole('tab', { name: '对话目录' })
    await openPanel.or(threadTab).filter({ visible: true }).first().waitFor()
    if (await openPanel.isVisible()) await openPanel.click()
    await threadTab.click()
    await page.getByTitle(fixtures[3], { exact: true }).click()
    await previewText('简约风季度工作汇报').waitFor({ timeout: 45000 })
    const presentation = page.locator('.pptx-viewer-shell')
    await presentation.getByText('简约风季度工作汇报', { exact: true }).first().click()
    for (const key of ['F5', 'p', 'P']) {
      const allowed = await presentation.evaluate((shell, key) =>
        shell.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, composed: true })), key)
      check(allowed, 'PPTX 仍拦截放映键 ' + key)
    }
    check(await presentation.locator('.pptx-slideshow-button').count() === 0, 'PPTX 仍显示放映按钮')
    await previewText('工作内容回顾').scrollIntoViewIfNeeded()
    await screenshot('agent-pptx-dark', page.locator('.agent-panel .preview-pane'))
    await page.getByTitle('全屏预览', { exact: true }).click()
    await page.locator('.fullscreen-preview-overlay').getByText('工作内容回顾', { exact: true }).first().waitFor()
    await page.locator('.fullscreen-preview-overlay').getByTitle('关闭', { exact: true }).click()

    // 只阻断渲染器模块，保留真实鉴权请求和 LibreOffice 转换。
    await page.route(viewerModule, (route) => route.abort())
    await open('/workspace')
    const pdfResponse = page.waitForResponse((response) =>
      response.url().endsWith('office_render=pdf'))
    await page.getByText(localFiles[0], { exact: true }).click()
    const pdf = await pdfResponse
    check(pdf.ok(), `PDF 回退失败: ${pdf.status()}`)
    check(pdf.headers()['content-type'] === 'application/pdf', '回退未返回 PDF')
    await page.waitForFunction(() => {
      const canvas = document.querySelector('.pdf-canvas')
      if (!canvas?.width || !canvas?.height) return false
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
      return pixels.some((value, index) => index % 4 === 0 && value < 240 && pixels[index + 3] > 0)
    })
    await screenshot('workspace-pdf-fallback-dark', page.locator('.agent-file-preview:visible').filter({ has: page.locator('.pdf-canvas') }))
    await scrollPdf('.workspace-file-preview-modal .file-content:has(.pdf-canvas)')
    await page.getByTitle('全屏预览', { exact: true }).click()
    await scrollPdf('.fullscreen-preview-overlay .file-content:has(.pdf-canvas)')

    check(previews.length >= 5, 'Office 原件请求缺失')
    for (const response of previews) {
      check(await response.request().headerValue('authorization'), '预览原件请求缺少鉴权')
    }
    return { screenshots: screenshotDir, officeResponses: previews.length }
  } catch (error) {
    await screenshot('failure')
    throw error
  } finally {
    await page.locator('#office-fixtures').evaluateAll((inputs) => inputs.forEach((input) => input.remove()))
    page.off('response', onResponse)
    await page.unroute(viewerModule)
    if (projectFilePath) {
      await api(`/api/viewer/filesystem/file?${query({ thread_id: threadId, path: projectFilePath })}`, { method: 'DELETE' })
    }
    if (threadId) await api(`/api/v1/agents/sessions/${threadId}/archive`, { method: 'POST' })
    if (kbId) await api(`/api/knowledge/knowledge-bases/${kbId}`, { method: 'DELETE' })
    for (const name of localFiles) {
      await api(`/api/workspace/file?${query({ path: '/' + name })}`, { method: 'DELETE' })
    }
    await page.evaluate((theme) => {
      if (theme === null) localStorage.removeItem('theme')
      else localStorage.setItem('theme', theme)
    }, before.theme)
    await page.setViewportSize({ width: before.width, height: before.height })
  }
}
