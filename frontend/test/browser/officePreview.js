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
    const response = await fetch(path, { headers, method, body }).catch((error) => {
      throw new Error(method + ' ' + path + ': ' + error.message)
    })
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
  const localFiles = []
  const previews = []
  const previewRequests = []
  let kbId
  let threadId
  let projectFilePath

  const pdfResponses = []
  const onResponse = (response) => {
    if (response.headers()['x-yuxi-preview-type'] === 'pdf') pdfResponses.push(response)
    if (response.headers()['x-yuxi-preview-type'] === 'office') previews.push(response)
  }
  const onRequest = (request) => {
    if (request.method() === 'GET' && request.url().startsWith(origin + '/api/workspace/file?')) {
      previewRequests.push(request.url())
    }
  }
  page.on('response', onResponse)
  page.on('request', onRequest)
  const viewerModule = /file-viewer-renderers/
  const spreadsheetParser = /\/(?:assets\/parser-[\w-]+\.js|sheetjs\/parser\.js)(?:\?|$)/
  const readDownload = async (button, filename) => {
    const event = page.waitForEvent('download')
    await button.click()
    const download = await event
    check(download.suggestedFilename() === filename, '下载文件名与上传原件不一致')
    const bytes = []
    for await (const chunk of await download.createReadStream()) {
      for (const byte of chunk) bytes.push(byte)
    }
    return bytes
  }

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
    await page.locator('#office-fixtures').setInputFiles([
      ...fixtures.map((name) => 'backend/test/data/' + name),
      'frontend/test/browser/fixtures/office-preview-native.pdf'
    ])
    const [wordBytes, sheetBytes] = await page.locator('#office-fixtures').evaluate(async (input) =>
      Promise.all([0, 1].map(async (index) => Array.from(new Uint8Array(await input.files[index].arrayBuffer())))))
    for (const index of [0, 1, 4]) {
      const name = suffix + '-' + (fixtures[index] || 'office-preview-native.pdf')
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
    const sheets = page.locator('.office-preview')
    const { promise: parserReady, resolve: releaseParser } = Promise.withResolvers()
    await page.route(spreadsheetParser, async (route) => {
      await parserReady
      await route.continue()
    })
    try {
      await page.getByRole('row', { name: new RegExp(localFiles[1]) }).press('Enter')
      const loading = sheets.getByRole('status').filter({ hasText: '正在解析 Excel 工作簿' })
      await loading.waitFor()
      check(await loading.innerText() === '正在解析 Excel 工作簿', '表格加载只显示一句话')
      check(await loading.locator('.loading-spinner').count() === 1, '表格加载只显示一个转圈')
      await screenshot('workspace-xls-loading', page.locator('.workspace-file-preview-modal .ant-modal'))
    } finally {
      releaseParser()
    }
    await sheets.getByRole('button', { name: '单行', exact: true }).waitFor({ timeout: 45000 })
    await page.unroute(spreadsheetParser)
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

    await page.setViewportSize({ width: 900, height: 590 })
    const grid = sheets.locator('.excel-wrapper')
    const zoom = () => grid.evaluate(element => element.__flyfishViewerZoomProvider.getState().scale)
    const wheel = async (deltaY) => {
      await grid.locator('.e-virt-table-stage').hover({ position: { x: 120, y: 100 } })
      await page.keyboard.down('Control')
      try {
        await page.mouse.wheel(0, deltaY)
      } finally {
        await page.keyboard.up('Control')
      }
    }
    const initialZoom = await zoom()
    await wheel(-100)
    await page.waitForFunction(initial => document.querySelector('.office-preview-frame')?.shadowRoot
      ?.querySelector('.excel-wrapper').__flyfishViewerZoomProvider.getState().scale > initial, initialZoom)
    await wheel(130)
    await page.waitForFunction(initial => document.querySelector('.office-preview-frame')?.shadowRoot
      ?.querySelector('.excel-wrapper').__flyfishViewerZoomProvider.getState().scale < initial, initialZoom)

    await page.setViewportSize({ width: 1280, height: 900 })
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

    // 阻断 Office 模块后只读一次原件，直接下载的浏览器字节与上传夹具一致。
    await page.route(viewerModule, (route) => route.abort())
    await open('/workspace')
    const wordPath = '/' + localFiles[0]
    const countWordReads = () => previewRequests.filter((url) => url.includes('path=' + encodeURIComponent(wordPath))).length
    const readsBeforeError = countWordReads()
    await page.getByText(localFiles[0], { exact: true }).click()
    await page.locator('.office-preview').getByRole('alert').waitFor()
    const downloadedWord = await readDownload(page.getByRole('button', { name: '下载原件', exact: true }), localFiles[0])
    check(downloadedWord.length === wordBytes.length && downloadedWord.every((byte, index) => byte === wordBytes[index]),
      'Office 下载字节与上传原件不一致')
    check(countWordReads() === readsBeforeError + 1,
      'Office 渲染失败后再次请求了预览')
    await screenshot('workspace-office-render-error', page.locator('.workspace-file-preview-modal .agent-file-preview'))
    await page.unroute(viewerModule)

    // Excel 解析器的加载失败由渲染器自行处理，全屏标题栏仍提供下载。
    await page.route(spreadsheetParser, (route) => route.abort())
    await open('/workspace')
    const parserFailed = page.waitForEvent('requestfailed', (request) => spreadsheetParser.test(request.url()))
    await page.getByText(localFiles[1], { exact: true }).click()
    await parserFailed
    await page.locator('.office-preview-frame .excel-wrapper .error').waitFor({ state: 'visible' })
    await page.getByTitle('全屏预览', { exact: true }).click()
    const downloadedSheet = await readDownload(
      page.locator('.fullscreen-preview-overlay').getByTitle('下载', { exact: true }), localFiles[1])
    check(downloadedSheet.length === sheetBytes.length && downloadedSheet.every((byte, index) => byte === sheetBytes[index]),
      'Excel 全屏下载字节与上传原件不一致')
    await page.unroute(spreadsheetParser)

    await open('/workspace')
    await page.getByText(localFiles[2], { exact: true }).click()
    const checkPdf = async (selector) => {
      await page.locator(selector).hover()
      await page.waitForFunction((selector) => {
        const cards = document.querySelector(selector)?.querySelectorAll('.pdf-page-card')
        if (cards?.length !== 2) return false
        const centerColor = (card) => {
          const canvas = card.querySelector('canvas')
          return canvas.getContext('2d').getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data
        }
        const [black, red] = Array.from(cards, centerColor)
        return black[0] < 16 && black[3] === 255 && red[0] > 235 && red[1] < 16 && red[3] === 255
      }, selector)
      await page.mouse.wheel(0, 1000)
      await page.waitForFunction((selector) => {
        const pane = document.querySelector(selector)
        const nextPage = pane?.querySelector('[data-page-number="2"]')
        return pane?.scrollTop > 0 && nextPage?.getBoundingClientRect().top < pane.getBoundingClientRect().bottom
      }, selector)
    }
    await checkPdf('.workspace-file-preview-modal .file-content:has(.pdf-canvas)')
    await page.getByTitle('全屏预览', { exact: true }).click()
    await checkPdf('.fullscreen-preview-overlay .file-content:has(.pdf-canvas)')
    check(pdfResponses.length === 1, '原生 PDF 未从普通预览读取')
    await screenshot('workspace-pdf-direct', page.locator('.fullscreen-preview-overlay'))


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
    page.off('request', onRequest)
    await page.unroute(viewerModule)
    await page.unroute(spreadsheetParser)
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
