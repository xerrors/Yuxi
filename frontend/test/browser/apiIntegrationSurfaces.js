// 在独立浏览器运行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/apiIntegrationSurfaces.js
// API 只使用合成数据；真实组件和浏览器证明错误状态、主题与剪贴板行为。
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  /** 使用独立 sRGB 亮度公式核对表面和正文。 */
  const luminance = color => {
    const [r, g, b] = color.match(/[\d.]+/g).slice(0, 3).map(value => {
      const channel = Number(value) / 255
      return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const contrast = (foreground, background) => {
    const a = luminance(foreground)
    const b = luminance(background)
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
  }
  const origin = 'http://localhost:5173'
  const overview = {
    apps: [{
      app_id: 'integration-fixture-with-a-long-app-name-to-check-layout-at-768px',
      end_user_count: 2, input_count: 3,
      keys: [
        { id: 1, name: '有效接入凭据', access_level: 'agents', is_valid: true },
        { id: 2, name: '过期凭据', access_level: 'knowledge', is_valid: false }
      ]
    }],
    totals: { app_count: 1, key_count: 1, end_user_count: 2, input_count: 3 }
  }
  let failed = false
  let legacyListRequests = 0
  const errors = []
  /** 等待真实主题渲染与有限动画结束，再读取样式和截图。 */
  const settle = async () => {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    await page.waitForFunction(() => document.getAnimations().every(animation =>
      animation.effect.getTiming().iterations === Infinity || animation.playState !== 'running'
    ))
  }
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', async route => {
    const request = route.request()
    check(request.method() === 'GET', '验证页面不得写入业务数据')
    const path = request.url().slice(origin.length).split('?')[0]
    let body = {}
    let status = 200
    if (path === '/api/user/apikey/apps') {
      status = failed ? 503 : 200
      body = failed ? { detail: '统计暂时不可用' } : overview
    } else if (path === '/api/user/apikey/') legacyListRequests++
    else if (path === '/api/system/config/options') body = { options: [] }
    else if (path === '/api/departments') body = []
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await page.route('**/review-missing.pdf', route => route.fulfill({ status: 404, body: 'missing' }))
  await page.goto(`${origin}/test/browser/settingsStyles.html`)
  await page.waitForFunction(() => window.settingsStyles)
  await page.evaluate(() => window.settingsStyles.mount('standalone', 'account'))
  await page.evaluate(async () => {
    const module = await (await fetch('/src/shared/model/theme.js')).text()
    const dependency = name => module.match(new RegExp(`from "([^"]*/${name}\\.js[^"]*)"`))[1]
    const { createApp, h, reactive, nextTick } = await import(dependency('vue'))
    const antUrl = module.match(/from "([^"]*ant-design-vue\.js[^"]*)"/)[1]
    const { default: Antd, ConfigProvider, Modal, Input, Select } = await import(antUrl)
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { default: Panel } = await import('/src/modules/settings/ui/ApiIntegrationPanel.vue')
    const { default: Mcp } = await import('/src/modules/knowledge/ui/KnowledgeMcpModal.vue')
    const { default: Markdown } = await import('/src/modules/workspace/ui/MarkdownPreview.vue')
    const { default: Pdf } = await import('/src/shared/ui/PdfPreview.vue')
    const { default: Tool } = await import('/src/modules/session/ui/tools/ToolRendererUnavailable.vue')
    const root = document.querySelector('#app')
    const pinia = root.__vue_app__.config.globalProperties.$pinia
    const theme = useThemeStore(pinia)
    const state = reactive({ mcp: false, controls: false, openedTab: '' })
    const host = document.createElement('main')
    host.id = 'integration-review'
    host.style.cssText = 'position:fixed;inset:0;z-index:900;overflow:auto;background:var(--bg-page);color:var(--gray-900)'
    document.body.append(host)
    root.style.display = 'none'
    const app = createApp({
      render: () => h(ConfigProvider, { theme: theme.currentTheme }, { default: () => [
        h(Panel, { style: 'height:auto' }),
        h('section', { style: 'padding:24px;display:grid;gap:16px' }, [
          h(Tool), h(Markdown, { content: '```js\nconsole.log(42)\n```\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20"><text x="5" y="15" fill="currentColor">SVG</text></svg>\n```', codeCopy: true }),
          h(Pdf, { url: '/review-missing.pdf', style: 'min-height:100px' })
        ]),
        h(Mcp, { open: state.mcp, knowledgeBaseId: 'kb-fixture', 'onUpdate:open': value => { state.mcp = value } }),
        h(Modal, { open: state.controls, title: '输入状态验证', footer: null }, { default: () => [
          h(Input, { id: 'review-enabled', value: '可编辑' }),
          h(Input, { id: 'review-disabled', value: '禁用', disabled: true }),
          h(Select, { id: 'review-select-enabled', value: 'a', options: [{ value: 'a', label: '可编辑' }] }),
          h(Select, { id: 'review-select-disabled', value: 'a', disabled: true, options: [{ value: 'a', label: '禁用' }] })
        ] })
      ] })
    })
    app.use(pinia)
    app.use(Antd)
    app.provide('settingsModal', { openSettingsModal: tab => { state.openedTab = tab } })
    app.mount(host)
    window.integrationReview = { state, theme, nextTick }
  })
  await page.getByText('有效接入凭据 · agents', { exact: true }).waitFor()
  await page.getByText('过期凭据 · knowledge · 无效', { exact: true }).waitFor()
  check(legacyListRequests === 0, '概览不应再请求截断的 Key 列表')
  const discovery = await page.request.get('http://localhost:5050/api/system/discovery')
  const knowledgeEnabled = discovery.ok()
    && (await discovery.json())?.capabilities?.features?.knowledge === true
  const endpoints = await page.evaluate(async (enabled) => {
    const { publicApiEndpoints } = await import('/src/apis/apikey_api.js')
    return publicApiEndpoints(enabled)
  }, knowledgeEnabled)
  check(new Set(endpoints.map(endpoint => `${endpoint.method} ${endpoint.path}`)).size === endpoints.length, '端点记录不唯一')
  // 以运行服务的 OpenAPI 为独立协议 oracle，错误方法或路径必须失败。
  const specification = await page.request.get('http://localhost:5050/openapi.json')
  check(specification.ok(), '无法读取运行服务的 OpenAPI')
  const { paths } = await specification.json()
  for (const endpoint of endpoints) {
    check(paths[endpoint.path]?.[endpoint.method.toLowerCase()], `展示了不存在的端点：${endpoint.method} ${endpoint.path}`)
  }
  const samples = []
  for (const dark of [false, true]) {
    await page.evaluate(async value => {
      window.integrationReview.theme.setTheme(value)
      await window.integrationReview.nextTick()
    }, dark)
    await page.locator('.markdown-code-copy-btn').waitFor()
    await settle()
    for (const width of [1280, 1024, 768]) {
      await page.setViewportSize({ width, height: 1000 })
      const sample = await page.evaluate(() => {
        const style = element => getComputedStyle(document.querySelector(element))
        return {
          overflow: document.querySelector('#integration-review').scrollWidth > innerWidth,
          toolBorder: style('.tool-renderer-unavailable').borderColor,
          toolText: style('.tool-renderer-unavailable').color,
          toolBackground: style('.tool-renderer-unavailable').backgroundColor,
          copyBackground: style('.markdown-code-copy-btn').backgroundColor,
          copyText: style('.markdown-code-copy-btn').color,
          tableHeader: style('#integration-review .ant-table-thead th').backgroundColor,
          tableText: style('#integration-review .ant-table-thead th').color
        }
      })
      check(!sample.overflow, `${width}px 出现整页横向溢出`)
      check(contrast(sample.toolText, sample.toolBackground) >= 4.5, '工具占位文字对比不足')
      check(contrast(sample.tableText, sample.tableHeader) >= 4.5, '接入表头文字对比度低于 4.5')
      if (dark) {
        check(luminance(sample.toolBorder) < 0.15, '工具占位的深色样式未生效')
        check(luminance(sample.copyBackground) < 0.1, '代码复制按钮在深色模式变成亮底')
        check(contrast(sample.copyText, sample.copyBackground) >= 4.5, '代码复制按钮文字对比不足')
        check(luminance(sample.tableHeader) < 0.1, '深色表头仍使用浅色背景')
      }
      samples.push({ dark, width, ...sample })
    }
    await page.screenshot({ path: `/tmp/yuxi-review-integration-${dark ? 'dark' : 'light'}.png`, animations: 'disabled' })
  }
  await page.locator('.pdf-error-view').waitFor()
  const errorColor = await page.locator('.pdf-error-view').evaluate(element => getComputedStyle(element).color)
  check(errorColor === 'rgb(255, 120, 117)', `PDF 错误提示未使用语义色：${errorColor}`)
  await page.locator('.svg-inline-render').hover()
  const svgButton = page.locator('.svg-action-btn').first()
  await svgButton.waitFor()
  await settle()
  const svgColor = await svgButton.evaluate(element => ({ color: getComputedStyle(element).color, background: getComputedStyle(element).backgroundColor }))
  check(Number(svgColor.background.match(/[\d.]+/g)[3] ?? 1) === 1, 'SVG 操作按钮应使用不透明主题底色')
  check(contrast(svgColor.color, svgColor.background) >= 4.5, 'SVG 操作按钮文字对比不足')
  await page.evaluate(() => { window.integrationReview.state.controls = true })
  await page.locator('#review-disabled').waitFor()
  await settle()
  const disabled = await page.evaluate(() => {
    const bg = selector => getComputedStyle(document.querySelector(selector)).backgroundColor
    return { input: [bg('#review-enabled'), bg('#review-disabled')] }
  })
  check(disabled.input[0] !== disabled.input[1], '禁用输入框丢失背景状态')
  const selectColors = await page.locator('.ant-modal .ant-select-selector').evaluateAll(elements => elements.map(element => getComputedStyle(element).backgroundColor))
  check(selectColors[0] !== selectColors[1], '禁用选择框丢失背景状态')
  await page.screenshot({ path: '/tmp/yuxi-review-disabled-dark.png', animations: 'disabled' })
  await page.evaluate(() => { window.integrationReview.state.controls = false; window.integrationReview.state.mcp = true })
  const mcp = page.getByRole('dialog', { name: 'MCP 接入' })
  await mcp.waitFor()
  await settle()
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin })
  await page.evaluate(() => Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false }))
  await mcp.getByRole('button', { name: '复制', exact: true }).first().click()
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  check(copied === `${origin}/api/mcp`, '非安全连接复制失败')
  await page.screenshot({ path: '/tmp/yuxi-review-mcp-dark.png', animations: 'disabled' })
  await mcp.getByRole('button', { name: '到设置中创建 API Key' }).click()
  await mcp.waitFor({ state: 'hidden' })
  check(await page.evaluate(() => window.integrationReview.state.openedTab) === 'apiKeys', '创建入口未打开凭据设置')
  failed = true
  await page.locator('#integration-review').getByRole('button', { name: '刷新' }).click()
  await page.locator('.settings-error .ant-alert').waitFor()
  await settle()
  check(await page.locator('.metric-card').count() === 0, '加载失败仍展示零值或过期统计')
  check(await page.locator('.integration-section').count() === 1, '加载失败丢失静态接入信息')
  await page.screenshot({ path: '/tmp/yuxi-review-integration-error.png', animations: 'disabled' })
  failed = false
  await page.locator('#integration-review').getByRole('button', { name: '刷新' }).click()
  await page.getByText('有效接入凭据 · agents', { exact: true }).waitFor()
  await page.evaluate(async () => {
    document.querySelector('#integration-review').style.display = 'none'
    document.querySelector('#app').style.display = ''
    await window.settingsStyles.mount('modal', 'account')
  })
  await page.locator('.settings-modal .settings-sider').waitFor({ state: 'attached' })
  await page.setViewportSize({ width: 1280, height: 1000 })
  for (const dark of [false, true]) {
    await page.evaluate(async value => {
      window.integrationReview.theme.setTheme(value)
      await window.integrationReview.nextTick()
    }, dark)
    await settle()
    const settings = await page.evaluate(() => {
      const bg = selector => getComputedStyle(document.querySelector(selector)).backgroundColor
      return { nav: bg('.settings-modal .settings-sider'), content: bg('.settings-modal .settings-content-wrapper') }
    })
    check(dark ? settings.content === 'rgb(34, 37, 41)' : settings.content === 'rgb(255, 255, 255)', '设置内容区主题背景不正确')
    if (!dark) check(settings.nav === 'rgb(245, 247, 247)', '浅色设置导航背景发生回归')
  }
  await page.screenshot({ path: '/tmp/yuxi-review-settings-dark.png', animations: 'disabled' })
  check(errors.length === 0, errors.join('\n'))
  return { samples, disabled: disabled.input, selectColors, copied, endpoints: endpoints.length }
}
