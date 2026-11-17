// 合成回执验证展示边界，不触发远端浏览器操作；真实部署版本由 Compose 固定。
// prettier-ignore
async originalPage => {
  const context = await originalPage.context().browser().newContext()
  const screenshots = []
  try {
    const page = await context.newPage()
    await page.goto(originalPage.url().split('/').slice(0, 3).join('/') + '/login')
    await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
    await page.evaluate(async () => {
      const source = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const read = { mode: 'text', text: '<script>window.__browserInjected = true</script>\n已加载的网页内容', offset: 0, total: 1000, next_offset: 80, truncated: true, cached: false, coverage: 'loaded_main_document', source_url: 'https://example.com/page' }
      const script = { value: false, output: ['读取完成', '<img src=x onerror=window.__browserInjected=true>'], steps_completed: 3 }
      const unknown = { status: 'outcome_unknown', result: { output: ['已完成点击'], steps_completed: 1 }, error: { message: '请检查页面再继续' } }
      const calls = [
        { name: 'browser_read', args: { selector: 'main' }, result: [{ type: 'text', text: JSON.stringify({ status: 'executed', result: read }) }] },
        { name: 'browser_read', args: { mode: 'tables' }, result: { status: 'executed', result: { ...read, mode: 'tables', rows: [{ table: 0, row: 0, cells: [{ text: '项目', row_span: 1, col_span: 1 }, { text: '结果', row_span: 1, col_span: 1 }] }, { table: 0, row: 1, cells: [{ text: '测试', row_span: 1, col_span: 1 }, { text: '已完成', row_span: 1, col_span: 1 }] }, { table: 1, row: 0, cells: [{ text: '第二张表格', row_span: 1, col_span: 1 }] }], total: 3, next_offset: null, truncated: false, cached: true, coverage: 'loaded_same_origin_frame' } } },
        { name: 'browser_wait', args: { selector: '#ready' }, result: { status: 'executed', result: { matched: true, state: 'visible' } } },
        { name: 'browser_wait', args: { text: '完成' }, result: { status: 'failed', error: { code: 'wait_timeout', message: '等待条件未在期限内满足' } } },
        { name: 'browser_exec', args: { code: 'return await page.evaluate(() => false)' }, result: { status: 'executed', result: script } },
        { name: 'browser_exec', args: { code: 'await page.locator("button").click()' }, tool_call_result: { status: 'error', content: [{ type: 'text', text: JSON.stringify(unknown) }] } },
        { name: 'browser_exec', args: { code: 'await page.wait({text:"完成"})' }, result: { status: 'dispatched', output: { stdout: '', stderr: '' } } },
        { name: 'command_status', args: { operation_id: 'fixture' }, result: { status: 'executed', result: { ...script, value: null, value_truncated: true, value_preview: '返回值预览' } } },
        { name: 'browser_read', args: {}, result: { status: 'executed', result: { ...read, text: '', total: 0, next_offset: null, truncated: false } } },
        { name: 'browser_read', args: {}, result: { status: 'rejected', error: { message: '未授权读取网页' }, result: read } },
        ...['press', 'select', 'hover', 'scroll'].map(type => ({ name: 'browser_act', args: { action: { type, element_ref: 'ref-42', text: '不进入标题的内容' } }, result: { status: 'executed', result: {} } }))
      ]
      const root = document.querySelector('#app').__vue_app__
      const host = document.createElement('main')
      host.id = 'abridge-new-browser-check'
      host.style = 'position:fixed;inset:0;z-index:100;padding:24px;overflow:auto;background:var(--gray-0);color:var(--gray-800)'
      document.body.append(host)
      const app = createApp({ render: () => h('div', { style: 'max-width:880px;margin:auto' }, calls.map((toolCall, index) => h('section', { 'data-case': index, style: 'margin-bottom:20px' }, [h(Tool, { toolCall, appearance: 'timeline', defaultExpanded: true })]))) })
      app.use(root.config.globalProperties.$pinia)
      app.use(root.config.globalProperties.$router)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.__browserToolsTheme = useThemeStore(root.config.globalProperties.$pinia)
    })
    const host = page.locator('#abridge-new-browser-check')
    const entry = index => host.locator(`[data-case="${index}"]`)
    for (const [index, label] of [[0, '读取网页内容'], [2, '等待网页条件'], [4, '执行浏览器脚本'], [10, '网页按键'], [11, '选择网页选项'], [12, '悬停网页元素'], [13, '滚动网页']]) {
      await entry(index).getByText(label, { exact: true }).waitFor()
    }
    await entry(0).getByText('还有后续内容', { exact: true }).waitFor()
    await entry(0).getByText('快照已截断', { exact: true }).waitFor()
    if (await entry(1).locator('table').count() !== 2) throw new Error('来源表格被合并')
    if (await entry(1).locator('caption').first().evaluate(node => getComputedStyle(node).captionSide) !== 'top') throw new Error('表格标题未显示在上方')
    await entry(1).getByText('第二张表格', { exact: true }).waitFor()
    await entry(2).getByText('等待条件已满足', { exact: true }).waitFor()
    if (await entry(3).getByText('等待条件已满足', { exact: true }).count()) throw new Error('超时被展示为匹配成功')
    await entry(4).getByLabel('浏览器脚本返回值', { exact: true }).getByText('false', { exact: true }).waitFor()
    await entry(5).getByText('执行结果未知', { exact: true }).waitFor()
    if (!(await entry(5).innerText()).includes('已完成 1 个步骤')) throw new Error('MCP错误信封丢失完成步骤')
    if (await entry(5).getByLabel('浏览器脚本返回值', { exact: true }).count()) throw new Error('未知结局显示了成功值')
    await entry(6).getByText('脚本处理中', { exact: true }).waitFor()
    await entry(7).getByText('查看浏览器执行结果', { exact: true }).waitFor()
    await entry(7).getByText('返回值已截断', { exact: true }).waitFor()
    await entry(8).getByText('未读取到网页文本', { exact: true }).waitFor()
    if (await entry(9).locator('.device-file-content').count()) throw new Error('拒绝回执显示了读取正文')
    if (await host.locator('script,img').count() || await page.evaluate(() => Boolean(window.__browserInjected))) throw new Error('设备内容被解释为HTML')
    if (await host.locator('details[open]').count()) throw new Error('原始回执不应默认展开')
    for (const width of [1440, 1024, 768]) {
      for (const theme of ['light', 'dark']) {
        await page.setViewportSize({ width, height: 1080 })
        await page.evaluate(theme => window.__browserToolsTheme.setTheme(theme), theme)
        for (const index of [0, 1, 5]) {
          const card = entry(index)
          await card.scrollIntoViewIfNeeded()
          if (await card.evaluate(node => node.scrollWidth > node.clientWidth)) throw new Error(`工具卡片横向溢出：${index}/${theme}/${width}`)
          const path = `/tmp/yuxi-browser-rendering-20261115/browser-${index}-${theme}-${width}.png`
          await card.screenshot({ path })
          screenshots.push(path)
        }
      }
    }
    return { passed: true, cases: 14, screenshots }
  } finally {
    await context.close()
  }
}
