// 在 Vite 页面执行：
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/officePreview.js
// 没有 playwright-cli 时，在仓库根目录执行：
// node frontend/test/browser/officePreview.runner.mjs
// 挂载知识库文件详情、工作区预览面板和 Agent 文件预览，用 backend/test/data 的真实 Office 夹具断言可见正文。
async page => {
  const { Buffer } = await import('node:buffer')
  const { existsSync, mkdirSync, readFileSync } = await import('node:fs')
  const { resolve } = await import('node:path')
  const fixtureDir = ['backend/test/data', 'frontend/../backend/test/data', '/workspace/backend/test/data']
    .map((dir) => resolve(dir))
    .find((dir) => existsSync(resolve(dir, '测试表格.xlsx')))
  if (!fixtureDir) throw new Error('未找到 backend/test/data/测试表格.xlsx，请从仓库根目录运行')
  const shotDir = existsSync('/opt/cursor/artifacts') ? '/opt/cursor/artifacts' : '/tmp'
  mkdirSync(shotDir, { recursive: true })
  const readFixture = (name) => readFileSync(resolve(fixtureDir, name))
  const xlsx = readFixture('测试表格.xlsx')
  const docx = readFixture('测试文档.docx')
  const pptx = readFixture('测试演示.pptx')
  const pdf = (() => {
    const stream = Buffer.from('BT /F1 18 Tf 36 100 Td (LibreOffice PDF fallback) Tj ET')
    const objects = [
      '1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n',
      '2 0 obj<< /Type /Pages /Count 1 /Kids [3 0 R] >>endobj\n',
      '3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n',
      `4 0 obj<< /Length ${stream.length} >>stream\n${stream.toString('latin1')}\nendstream\nendobj\n`,
      '5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n'
    ].map((item) => Buffer.from(item, 'latin1'))
    let body = Buffer.from('%PDF-1.4\n')
    const offsets = [0]
    for (const object of objects) {
      offsets.push(body.length)
      body = Buffer.concat([body, object])
    }
    let xref = `xref\n0 ${offsets.length}\n0000000000 65535 f \n`
    for (const offset of offsets.slice(1)) xref += `${String(offset).padStart(10, '0')} 00000 n \n`
    xref += `trailer<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${body.length}\n%%EOF\n`
    return Buffer.concat([body, Buffer.from(xref)])
  })()
  const check = (condition, message) => {
    if (!condition) throw new Error(message)
  }
  const shot = (name) => resolve(shotDir, name)
  const external = []
  const previewRequests = []
  page.on('request', (request) => {
    const url = request.url()
    if (url.startsWith('data:') || url.startsWith('blob:')) return
    const host = new URL(url).hostname
    if (host !== '127.0.0.1' && host !== 'localhost') external.push(url)
  })
  let releaseRenderer = () => {}
  const rendererGate = new Promise((resolveRenderer) => {
    releaseRenderer = resolveRenderer
  })
  let releasePdf = () => {}
  const pdfGate = new Promise((resolvePdf) => {
    releasePdf = resolvePdf
  })
  await page.route('**/*file-viewer-renderers*', async (route) => {
    await rendererGate
    await route.continue()
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = request.url()
    const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    const authorization = request.headers().authorization || ''
    if (path === '/api/workspace/knowledge/file' || (path === '/api/workspace/file' && url.includes('office_render=pdf'))) {
      previewRequests.push({ url, authorization })
    }
    if (path.endsWith('/basic')) {
      return route.fulfill({
        json: {
          meta: {
            file_id: 'sheet',
            filename: '测试表格.xlsx',
            status: 'uploaded',
            has_original_file: true,
            has_parsed_markdown: false,
            is_folder: false
          }
        }
      })
    }
    if (path.endsWith('/content')) return route.fulfill({ json: { content: '', lines: [] } })
    if (path === '/api/workspace/knowledge/file') {
      return route.fulfill({
        status: 200,
        headers: {
          'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'x-yuxi-preview-type': 'office',
          'x-yuxi-preview-filename': encodeURIComponent('测试表格.xlsx')
        },
        body: xlsx
      })
    }
    if (path === '/api/workspace/file' && url.includes('office_render=pdf')) {
      await pdfGate
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/pdf', 'x-yuxi-preview-type': 'pdf' },
        body: pdf
      })
    }
    return route.fulfill({ json: {} })
  })
  const setAgentFile = (b64, name, type, extra = {}) =>
    page.evaluate(
      ({ b64, name, type, extra }) => {
        const check = window.officePreviewCheck
        check.stage.value = 'agent'
        check.status.value = ''
        check.filePath.value = `/${name}`
        check.file.value = {
          previewType: 'office',
          supported: true,
          filename: name,
          name,
          path: `/${name}`,
          previewFile: check.toFile(b64, name, type),
          ...extra
        }
      },
      { b64, name, type, extra }
    )
  const waitForPaintedText = (needle, rootSelector = 'body', timeout = 90000) =>
    page.waitForFunction(
      ({ expected, rootSelector }) => {
        const root = rootSelector === 'body' ? document.body : document.querySelector(rootSelector)
        if (!root) return false
        const range = document.createRange()
        const walk = (node) => {
          if (!node || node.nodeName === 'STYLE' || node.nodeName === 'SCRIPT') return false
          if (node.shadowRoot && walk(node.shadowRoot)) return true
          if (node.nodeType === Node.TEXT_NODE && (node.textContent || '').includes(expected)) {
            range.selectNodeContents(node)
            const rect = range.getBoundingClientRect()
            if (rect.width > 0 && rect.height > 0) return true
          }
          for (const child of node.childNodes || []) if (walk(child)) return true
          return false
        }
        return walk(root)
      },
      { expected: needle, rootSelector },
      { timeout }
    )
  const originalViewport = page.viewportSize()
  let originalTheme = null
  try {
    if (!page.url().startsWith('http://127.0.0.1:5173')) {
      await page.goto('http://127.0.0.1:5173/')
    }
    await page.waitForSelector('#app')
    originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.evaluate(async () => {
      const main = await (await fetch('/src/app/main.js')).text()
      const { createApp, h, ref } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
      const { default: AgentFilePreview } = await import('/src/modules/session/ui/workspace/AgentFilePreview.vue')
      const { default: WorkspacePreviewPane } = await import('/src/modules/workspace/ui/WorkspacePreviewPane.vue')
      const { default: FileDetailModal } = await import('/src/modules/knowledge/ui/FileDetailModal.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const { configureApiAuth } = await import('/src/apis/auth_context.js')
      const { useUserStore } = await import('/src/modules/identity/model/user.js')
      const root = document.querySelector('#app').__vue_app__
      const user = useUserStore(root.config.globalProperties.$pinia)
      configureApiAuth({ getToken: () => 'office-preview-check', isAdmin: () => true })
      const pinia = createPinia()
      const theme = useThemeStore(pinia)
      theme.setTheme('light')
      const host = document.createElement('div')
      host.id = 'office-preview-check'
      host.style.cssText = 'position:fixed;inset:0;z-index:20;display:flex;background:var(--gray-0)'
      document.body.append(host)
      const stage = ref('agent')
      const status = ref('loading')
      const file = ref(null)
      const filePath = ref('/测试表格.xlsx')
      const paneFile = ref(null)
      const panePath = ref('')
      const paneLoading = ref(false)
      const detailOpen = ref(false)
      const bytesFrom = (b64) => {
        const binary = atob(b64)
        const bytes = new Uint8Array(binary.length)
        for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
        return bytes
      }
      const toFile = (b64, name, type) => new File([bytesFrom(b64)], name, { type })
      const app = createApp({
        setup() {
          return () =>
            h(
              'div',
              { style: { display: 'flex', flex: '1', minWidth: '0', minHeight: '0', height: '100%' } },
              [
                h(root._context.components.AConfigProvider, { theme: theme.currentTheme, style: { display: 'flex', flex: '1', minWidth: '0' } }, () => {
                  if (stage.value === 'workspace') {
                    return h(WorkspacePreviewPane, {
                      file: paneFile.value,
                      filePath: panePath.value,
                      loading: paneLoading.value,
                      style: { width: '100%', height: '100%' }
                    })
                  }
                  if (stage.value === 'knowledge') {
                    return h(FileDetailModal, { embedded: true, open: detailOpen.value, kbId: 'kb', fileId: 'sheet' })
                  }
                  return h(AgentFilePreview, {
                    file: file.value,
                    filePath: filePath.value,
                    status: status.value,
                    fullHeight: true,
                    showFileIcon: false,
                    borderless: true,
                    showClose: false,
                    showDownload: true,
                    showFullscreen: true,
                    containerClass: 'side-preview-shell',
                    contentClass: 'side-file-content'
                  })
                })
              ]
            )
        }
      })
      app.use(pinia)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.__officeNotes = []
      window.__officeTimer = setInterval(() => {
        const text = document.body.innerText || ''
        for (const needle of ['正在加载 Office 预览', '正在加载文件内容', '正在使用服务端转换']) {
          if (text.includes(needle) && !window.__officeNotes.includes(needle)) window.__officeNotes.push(needle)
        }
      }, 30)
      window.officePreviewCheck = {
        stage,
        status,
        file,
        filePath,
        paneFile,
        panePath,
        paneLoading,
        detailOpen,
        theme,
        toFile,
        bytesFrom,
        app,
        host,
        restoreAuth: () =>
          configureApiAuth({
            getToken: () => user.token,
            isAdmin: () => user.isAdmin,
            isSuperAdmin: () => user.isSuperAdmin
          })
      }
    })
    const host = page.locator('#office-preview-check')
    await host.getByText('正在加载文件内容...').waitFor()
    await host.screenshot({ path: shot('office-agent-loading.png') })
    await setAgentFile(
      xlsx.toString('base64'),
      '测试表格.xlsx',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    await host.getByText('正在加载 Office 预览...').waitFor({ timeout: 20000 })
    await host.screenshot({ path: shot('office-agent-office-loading.png') })
    releaseRenderer()
    await waitForPaintedText('李果果')
    await waitForPaintedText('个人所得税')
    check((await host.getByText('当前文件暂不支持预览').count()) === 0, '表格被显示成不支持预览')
    await page.waitForTimeout(400)
    await host.screenshot({ path: shot('office-agent-xlsx-light-1280.png') })
    await page.setViewportSize({ width: 768, height: 800 })
    await page.waitForTimeout(300)
    const narrowBox = await host.boundingBox()
    const pageFits = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)
    check(narrowBox && narrowBox.x >= -1 && narrowBox.x + narrowBox.width <= 769, `768px 预览超出视口：${JSON.stringify(narrowBox)}`)
    check(pageFits, '768px 页面出现横向滚动')
    await host.screenshot({ path: shot('office-agent-xlsx-light-768.png') })
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.evaluate(() => window.officePreviewCheck.theme.setTheme('dark'))
    check(await page.evaluate(() => document.documentElement.classList.contains('dark')), '深色主题未写到 document')
    await setAgentFile(docx.toString('base64'), '测试文档.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    await waitForPaintedText('述职报告')
    await page.waitForTimeout(400)
    await host.screenshot({ path: shot('office-agent-docx-dark.png') })
    await page.locator('button[title="全屏预览"]').click()
    const overlay = page.locator('.fullscreen-preview-overlay')
    await overlay.waitFor()
    await waitForPaintedText('述职报告', '.fullscreen-preview-overlay')
    check((await overlay.getByText('previewType').count()) === 0, '全屏把文件对象渲染成了 JSON')
    check((await host.getByText('当前文件暂不支持预览').count()) === 0, '全屏打开后内联区域误显示不支持预览')
    await page.waitForTimeout(500)
    await overlay.screenshot({ path: shot('office-agent-docx-fullscreen-dark.png') })
    await overlay.locator('button[title="关闭"]').click()
    await overlay.waitFor({ state: 'hidden' })
    await page.evaluate(() => window.officePreviewCheck.theme.setTheme('light'))
    await page.evaluate(() => {
      const check = window.officePreviewCheck
      check.detailOpen.value = true
      check.stage.value = 'knowledge'
    })
    await page.waitForFunction(
      () => {
        const hostNode = document.querySelector('#office-preview-check')
        const section = hostNode?.querySelector('.file-detail-embedded')
        const name = section?.querySelector('.file-name')
        if (!hostNode || !section || !name?.textContent?.includes('测试表格.xlsx')) return false
        // 夹具宿主不是知识库页面的真实布局，嵌入详情需要被拉满后文件名和表格才有尺寸。
        let node = section
        while (node && node !== hostNode.parentElement) {
          node.style.display = 'flex'
          node.style.flexDirection = 'column'
          node.style.width = '100%'
          node.style.height = '100%'
          node.style.minWidth = '0'
          node.style.minHeight = '0'
          node = node.parentElement
        }
        const rect = name.getBoundingClientRect()
        return rect.width > 0 && rect.height > 0
      },
      null,
      { timeout: 20000 }
    )
    await host.getByRole('button', { name: /当前视图为源文件/ }).waitFor()
    await waitForPaintedText('李果果')
    const knowledgeRequest = previewRequests.find((item) => item.url.includes('/api/workspace/knowledge/file'))
    check(Boolean(knowledgeRequest), '知识库源文件预览没有发出请求')
    check(
      knowledgeRequest.authorization === 'Bearer office-preview-check',
      `知识库预览请求缺少夹具 token：${knowledgeRequest.authorization}`
    )
    await page.waitForTimeout(400)
    await host.screenshot({ path: shot('office-knowledge-xlsx-light.png') })
    await page.evaluate(() => {
      const check = window.officePreviewCheck
      check.paneFile.value = null
      check.paneLoading.value = false
      check.stage.value = 'workspace'
    })
    await host.getByText('选择文件以预览').waitFor()
    await host.screenshot({ path: shot('office-workspace-empty.png') })
    await page.evaluate(() => {
      window.officePreviewCheck.paneLoading.value = true
    })
    await host.getByText('正在加载文件内容...').waitFor()
    await host.screenshot({ path: shot('office-workspace-loading.png') })
    await page.evaluate(() => window.officePreviewCheck.theme.setTheme('dark'))
    await page.evaluate(
      ({ b64, name, type }) => {
        const check = window.officePreviewCheck
        check.paneLoading.value = false
        check.panePath.value = `/${name}`
        check.paneFile.value = {
          previewType: 'office',
          supported: true,
          filename: name,
          name,
          path: `/${name}`,
          previewFile: check.toFile(b64, name, type)
        }
      },
      {
        b64: pptx.toString('base64'),
        name: '测试演示.pptx',
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
      }
    )
    await waitForPaintedText('简约风季度工作汇报')
    await page.waitForTimeout(500)
    await host.screenshot({ path: shot('office-workspace-pptx-dark.png') })
    await page.evaluate(() => window.officePreviewCheck.theme.setTheme('light'))
    const visibleHostText = () =>
      page.evaluate(() => {
        const root = document.querySelector('#office-preview-check')
        const parts = []
        const walk = (node) => {
          if (!node || node.nodeName === 'STYLE' || node.nodeName === 'SCRIPT') return
          if (node.shadowRoot) walk(node.shadowRoot)
          if (node.nodeType === Node.TEXT_NODE) parts.push(node.textContent || '')
          for (const child of node.childNodes || []) walk(child)
        }
        walk(root)
        return parts.join('\n')
      })
    await setAgentFile(Buffer.from('not-an-xlsx').toString('base64'), '损坏.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    await waitForPaintedText('损坏.xlsx', '#office-preview-check')
    await page.waitForTimeout(1000)
    const brokenSheetText = await visibleHostText()
    await host.screenshot({ path: shot('office-agent-xlsx-unreadable.png') })
    check(!brokenSheetText.includes('正在使用服务端转换'), 'xlsx 不应进入服务端转换')
    check(!previewRequests.some((item) => item.url.includes('office_render=pdf')), 'xlsx 不应请求 LibreOffice PDF')
    const invalidDocx = Buffer.from(
      'UEsDBBQAAAAAAIBoeV0Uo0WfVAAAAFQAAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbDxUeXBlcyB4bWxucz0iaHR0cDovL3NjaGVtYXMub3BlbnhtbGZvcm1hdHMub3JnL3BhY2thZ2UvMjAwNi9jb250ZW50LXR5cGVzIj48L1R5cGVzPlBLAQIUAxQAAAAAAIBoeV0Uo0WfVAAAAFQAAAATAAAAAAAAAAAAAACAAQAAAABbQ29udGVudF9UeXBlc10ueG1sUEsFBgAAAAABAAEAQQAAAIUAAAAAAA==',
      'base64'
    )
    await setAgentFile(invalidDocx.toString('base64'), '损坏.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    let brokenDocText = ''
    const docDeadline = Date.now() + 45000
    while (Date.now() < docDeadline) {
      brokenDocText = await visibleHostText()
      if (brokenDocText.includes('正在使用服务端转换') || brokenDocText.includes('不是有效的 DOCX')) break
      await page.waitForTimeout(200)
    }
    check(
      brokenDocText.includes('正在使用服务端转换'),
      `损坏的 docx 没有进入服务端转换：${brokenDocText.slice(0, 1200)}`
    )
    await host.screenshot({ path: shot('office-agent-docx-fallback-loading.png') })
    releasePdf()
    const pdfCanvas = page.locator('.pdf-preview canvas')
    await pdfCanvas.waitFor({ timeout: 60000 })
    await page.waitForTimeout(500)
    const pdfInk = await pdfCanvas.evaluate((canvas) => {
      const context = canvas.getContext('2d')
      const sample = context.getImageData(0, 0, canvas.width, canvas.height).data
      let ink = 0
      for (let index = 0; index < sample.length; index += 16) {
        if (sample[index] < 240 || sample[index + 1] < 240 || sample[index + 2] < 240) ink += 1
      }
      return { ink, width: canvas.width, height: canvas.height }
    })
    check(pdfInk.ink > 30, `PDF 画布没有可见笔画：${JSON.stringify(pdfInk)}`)
    await host.screenshot({ path: shot('office-agent-docx-fallback-pdf.png') })
    const pdfRequest = previewRequests.find((item) => item.url.includes('office_render=pdf'))
    check(Boolean(pdfRequest), 'docx 回退没有请求 office_render=pdf')
    check(pdfRequest.authorization === 'Bearer office-preview-check', `PDF 回退请求缺少夹具 token：${pdfRequest.authorization}`)
    const notes = await page.evaluate(() => window.__officeNotes)
    check(notes.includes('正在加载 Office 预览'), `没有观察到 Office 加载文案：${JSON.stringify(notes)}`)
    check(notes.includes('正在使用服务端转换'), `没有观察到服务端转换文案：${JSON.stringify(notes)}`)
    const cdn = external.filter((url) => /cdn\.jsdelivr|unpkg\.com|cdnjs\.cloudflare|fonts\.googleapis|fonts\.gstatic/i.test(url))
    check(cdn.length === 0, `Office 预览请求了外部 CDN：${cdn.join(', ')}`)
    return { notes, external, screenshots: shotDir }
  } finally {
    await page
      .evaluate((previousTheme) => {
        clearInterval(window.__officeTimer)
        const check = window.officePreviewCheck
        if (!check) return
        check.theme.setTheme(previousTheme || 'light')
        if (previousTheme === null) localStorage.removeItem('theme')
        check.app.unmount()
        check.host.remove()
        check.restoreAuth()
        delete window.officePreviewCheck
      }, originalTheme)
      .catch(() => {})
    if (originalViewport) await page.setViewportSize(originalViewport)
    await page.unroute('**/api/**').catch(() => {})
    await page.unroute('**/*file-viewer-renderers*').catch(() => {})
  }
}
