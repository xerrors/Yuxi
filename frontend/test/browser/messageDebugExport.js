// 确定性审计 replay：在真实 Vite 页面验证六种 Turn 导出和 Content 展示。
// 执行：playwright-cli -s=turn-export run-code --filename=frontend/test/browser/messageDebugExport.js
async (page) => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const long = ' a'.repeat(400)
  const result = { status: 'executed', result: { apps: [{ name: '示例编辑器', running: true }], text: long } }
  const content = JSON.stringify([{ type: 'text', text: JSON.stringify(result) }])
  const common = { turn_id: 'turn-a', run_id: 'run-a', execution_status: 'completed' }
  const audits = [
    { ...common, id: 1, type: 'human', input_id: 'input-a', content: '查看应用列表\n保留完整问答' },
    { ...common, id: 2, type: 'ai', operation_id: 'model-a', content: '我先查看应用列表。', reasoning_content: '仅完整档保留推理',
      tool_calls: [{ id: 'call-a', name: 'computer_apps', args: { text: long } }], content_blocks: ['重复模型正文'] },
    { ...common, id: 3, type: 'tool', operation_id: 'call-a', tool_name: 'computer_apps', tool_input: { text: long }, content, tool_output: result },
    { ...common, id: 4, type: 'ai', operation_id: 'model-tools', content: '', tool_calls: [{ id: 'pending', name: 'pending_tool', args: { path: '/example' } }] },
    { ...common, id: 5, run_id: 'run-b', type: 'ai', operation_id: 'model-final', content: '已找到示例编辑器。\n<script>unsafe()</script>', reasoning_content: '内部推理' },
    { id: 6, turn_id: 'turn-other', run_id: 'run-other', type: 'human', input_id: 'input-other', content: '其他 Turn 不应导出' }
  ]
  const runs = ['run-a', 'run-b', 'run-other'].map((run_id, index) => ({ run_id, turn_id: index === 2 ? 'turn-other' : 'turn-a', status: 'completed' }))
  const routeHandler = async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    await route.fulfill({ json: path.endsWith('/audits')
      ? { audits, runs, turns: [{ turn_id: 'turn-a', status: 'completed' }], truncated: false }
      : { data: [], config: {}, first_run: false } })
  }
  await page.unroute('**/api/**')
  await page.route('**/api/**', routeHandler)
  await page.goto('http://localhost:5173')
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const { default: Panel } = await import('/src/modules/session/ui/MessageDebugPanel.vue')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'turn-export-fixture', isSuperAdmin: () => true })
    const root = document.querySelector('#app').__vue_app__
    const host = document.createElement('div')
    host.id = 'turn-export-check'
    Object.assign(host.style, { position: 'fixed', inset: '16px', zIndex: 1000, overflow: 'hidden', background: 'var(--gray-0)' })
    document.body.append(host)
    const pinia = createPinia()
    const theme = useThemeStore(pinia)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme },
      () => h(Panel, { threadId: 'thread-export-fixture', active: true })) })
    app.use(pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    window.turnExportCheck = { app, host, theme, copies: {} }
  })
  const panel = page.locator('#turn-export-check .message-debug-panel')
  const inspector = panel.getByRole('complementary', { name: '记录详情' })
  const menu = panel.getByRole('button', { name: 'Turn 导出选项', exact: true })
  await panel.locator('.message-row').nth(5).waitFor()
  await panel.locator('.message-row').nth(2).click()
  await page.locator('.ant-message-notice').waitFor({ state: 'hidden' })
  const displayed = await inspector.locator('.inspector-field-content dd').textContent()
  check(displayed === JSON.stringify(result, null, 2), 'Content 未解开文本块或格式化 JSON')
  check(!displayed.includes('\\n') && !displayed.includes('\\"'), 'Content 仍显示包装转义符')
  await inspector.getByRole('button', { name: '数据', exact: true }).click()
  check(await inspector.locator('.json-tree-viewer').isVisible(), '原始数据页丢失')
  await inspector.getByRole('button', { name: '概览', exact: true }).click()
  // 筛选后从工具详情导出，仍须包含所属 Turn 的全部已加载记录和 Run。
  await panel.getByRole('searchbox').fill('computer_apps')
  check(await panel.locator('.message-row').count() < 5, '搜索未生效')
  for (const [detail, label] of [['full', '完整'], ['compact', '精简'], ['minimal', '极简']]) {
    await page.evaluate(() => navigator.clipboard.writeText('__pending_turn_export__'))
    await menu.click()
    await page.getByRole('menuitem', { name: '下载极简Turn', exact: true }).waitFor()
    check(await page.getByRole('menuitem').count() === 6, '不是六个导出选项')
    await page.getByRole('menuitem', { name: `复制${label}Turn`, exact: true }).click()
    await page.waitForFunction(() => document.querySelector('[aria-label="Turn 导出选项"]')?.getAttribute('aria-busy') === 'false')
    const copied = await page.evaluate(async detail => {
      const text = await navigator.clipboard.readText()
      window.turnExportCheck.copies[detail] = text
      return text
    }, detail)
    check(copied !== '__pending_turn_export__', `剪贴板未更新：${detail}`)
    const data = JSON.parse(copied)
    if (detail === 'full') {
      check(JSON.stringify(data.records) === JSON.stringify(audits.slice(0, 5)), '完整导出并非所属 Turn 全部原始记录')
      check(data.runs.length === 2 && data.turn_id === 'turn-a', '完整 Turn 缺少 Run 或混入其他 Turn')
    } else {
      check(!copied.includes('reasoning') && !copied.includes('推理') && !copied.includes('model_call'), '精简档混入推理或冗余模型调用')
      check(!copied.includes('operation_id') && !copied.includes('run_id') && !copied.includes('其他 Turn'), '精简档混入内部 ID 或其他 Turn')
      if (detail === 'compact') {
        const tool = data.messages.find(message => message.role === 'tool')
        check(tool.arguments.includes('已省略中间内容') && tool.result.includes('已省略中间内容'), '工具参数或结果未截断')
        check(data.messages.filter(message => message.tool_calls).length === 1, '重复工具声明未消除或未执行调用丢失')
      } else {
        check(data.messages.length === 3, '极简档没有保留全部正文')
        check(data.messages.every(message => ['user', 'assistant'].includes(message.role) && Object.keys(message).length === 2), '极简档包含正文以外字段')
        check(!copied.includes('computer_apps') && !copied.includes('tool_calls'), '极简档包含工具调用')
      }
    }
    await menu.click()
    const downloaded = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: `下载${label}Turn`, exact: true }).click()
    const download = await downloaded
    check(download.suggestedFilename() === `turn-1-${detail}.json`, '下载文件名未标明档位')
    const stream = await download.createReadStream()
    stream.setEncoding('utf8')
    let downloadedText = ''
    for await (const chunk of stream) downloadedText += chunk
    check(downloadedText === copied, `下载内容与复制内容不一致：${detail}`)
  }
  await panel.getByRole('searchbox').fill('')
  await panel.locator('.message-row').nth(4).click()
  check(await inspector.locator('script').count() === 0, '正文被执行为 HTML')
  check((await inspector.locator('.inspector-field-content dd').textContent()).includes('\n<script>'), '正文换行或字面量 HTML 丢失')
  await panel.locator('.message-row').nth(2).click()
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => window.turnExportCheck.theme.setTheme(theme), theme)
    for (const width of [1440, 768]) {
      await page.setViewportSize({ width, height: 900 })
      await menu.click()
      await page.getByRole('menuitem', { name: '下载极简Turn', exact: true }).waitFor()
      await page.waitForFunction(() => {
        const box = document.querySelector('[role="menu"]')?.getBoundingClientRect()
        const dropdown = document.querySelector('.ant-dropdown')
        return box?.width > 0 && box.x >= 0 && box.right <= innerWidth &&
          !/slide-up-(enter|appear|leave)/.test(dropdown.className)
      })
      const box = await page.getByRole('menu').boundingBox()
      check(box.x >= 0 && box.x + box.width <= width, '菜单超出视口')
      await page.screenshot({ path: `/tmp/yuxi-turn-export-${theme}-${width}.png` })
      await menu.click()
      await page.getByRole('menu').waitFor({ state: 'hidden' })
    }
  }
  await page.evaluate(() => window.turnExportCheck.theme.setTheme('light'))
  await page.setViewportSize({ width: 1440, height: 900 })
  // 通过键盘打开菜单并执行首项，检查图标按钮可达且具备操作名称。
  await menu.focus()
  await page.evaluate(() => navigator.clipboard.writeText('__pending_turn_export__'))
  await page.keyboard.press('Enter')
  await page.getByRole('menuitem', { name: '复制完整Turn', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => document.querySelector('[aria-label="Turn 导出选项"]')?.getAttribute('aria-busy') === 'false')
  check(await page.evaluate(() => navigator.clipboard.readText()) === await page.evaluate(() => window.turnExportCheck.copies.full), '键盘菜单未执行完整复制')
  return { contentRendered: true, sixActions: true, selectedToolExportsWholeTurn: true, filteredScope: true, keyboard: true,
    screenshots: ['light-1440', 'dark-1440', 'light-768', 'dark-768'] }
}
