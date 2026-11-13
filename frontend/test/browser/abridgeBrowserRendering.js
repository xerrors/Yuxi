// 真实组件展示 Abridge 浏览器合成回执，不连接设备或执行浏览器操作。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/abridgeBrowserRendering.js
// prettier-ignore
async originalPage => {
  const context = await originalPage.context().browser().newContext()
  const evidence = []
  try {
    const page = await context.newPage()
    await page.goto(originalPage.url().split('/').slice(0, 3).join('/'))
    await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
    await page.evaluate(async () => {
      const source = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const image = await (await fetch('/test/browser/fixtures/imgA.png')).blob()
      const data = await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.readAsDataURL(image) })
      const receipt = { operation_id: 'operation-fixture', status: 'executed', result: { url: 'https://example.com/', title: '浏览器演示页', observation: { url: 'https://example.com/', title: '浏览器演示页', text: '原始网页观察正文' } } }
      const mixed = [{ type: 'input_text', text: JSON.stringify(receipt) }, { type: 'image', base64: data, mime_type: 'image/png' }]
      const calls = [
        { name: 'devices_list', args: {}, result: { devices: [{ id: 'device-fixture', name: '演示浏览器设备' }] } },
        { name: 'session_open', args: { capabilities: ['browser'] }, result: { status: 'active', capabilities: ['browser'] } },
        { name: 'browser_open', args: { session_id: 'session-fixture', url: 'https://example.com/', screenshot: true }, result: JSON.stringify(mixed) },
        { name: 'browser_observe', args: { query: '导航' }, result: receipt },
        { name: 'browser_act', args: { action: { type: 'click', element_ref: 'ref-42' } }, result: receipt },
        { name: 'browser_act', args: { action: { type: 'fill', element_ref: 'ref-43', text: '示例输入' } }, result: mixed },
        { name: 'browser_open', args: { url: 'https://example.com/failure' }, result: [{ type: 'text', text: '{"status":"rejected","error":"未获得浏览器权限"}' }, mixed[1]] },
        { name: 'browser_act', args: {}, result: { status: 'outcome_unknown' } },
        { name: 'command_execute', args: { command: 'example-command' }, result: { status: 'executed', result: { stdout: 'Finder, Chrome, Code', exit_code: 0 } } },
        { name: 'command_execute', args: { command: 'failed-command' }, result: { status: 'executed', result: { stdout: '部分输出', stderr: '命令失败', exit_code: 1 } } }
      ]
      const root = document.querySelector('#app').__vue_app__
      const host = document.createElement('main')
      host.id = 'abridge-browser-check'
      host.style = 'position:fixed;inset:0;z-index:100;padding:32px;overflow:auto;background:var(--gray-0);color:var(--gray-800)'
      document.body.append(host)
      const app = createApp({ render: () => h('div', { style: 'max-width:880px;margin:auto' }, [
        h('h2', { style: 'margin-bottom:24px' }, 'Abridge 浏览器工具'),
        ...calls.map((call, index) => h('section', { 'data-case': index, style: 'margin-bottom:12px' }, [h(Tool, { toolCall: call, appearance: 'timeline' })]))
      ]) })
      app.use(root.config.globalProperties.$pinia)
      app.use(root.config.globalProperties.$router)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.__abridgeTheme = useThemeStore(root.config.globalProperties.$pinia)
    })
    const host = page.locator('#abridge-browser-check')
    for (const [index, text] of [[0, '查看设备'], [1, '连接浏览器'], [2, '打开网页'], [3, '查看网页'], [4, '点击网页元素'], [5, '填写网页内容'], [6, '执行失败'], [7, '执行结果未知']]) {
      await host.locator(`[data-case="${index}"]`).getByText(text, { exact: true }).waitFor()
    }
    const opened = host.locator('[data-case="2"]')
    await opened.locator('.tool-header').click()
    await opened.locator('.tool-result-image img').waitFor()
    await opened.locator('.tool-result-image img').evaluate(async img => { await img.decode() })
    if (!await opened.getByText('参数:', { exact: true }).isVisible()) throw new Error('参数入口被移除')
    if ((await opened.innerText()).includes('原始网页观察正文')) throw new Error('正文被强行展开')
    const summaryStyle = await opened.locator('summary').evaluate(node => ({ fontSize: getComputedStyle(node).fontSize, lineHeight: getComputedStyle(node).lineHeight }))
    if (summaryStyle.fontSize !== '12px' || summaryStyle.lineHeight !== '20px') throw new Error(`原始结果入口未沿用紧凑详情字号：${JSON.stringify(summaryStyle)}`)
    await opened.locator('summary').click()
    if (!(await opened.innerText()).includes('原始网页观察正文')) throw new Error('折叠的原始 JSON 丢失')
    await opened.locator('summary').click()
    await opened.locator('.tool-result-image').click()
    await page.locator('.chat-image-viewer-image').waitFor()
    await page.getByRole('button', { name: '关闭图片预览' }).click()
    for (const index of [0, 3]) {
      const entry = host.locator(`[data-case="${index}"]`)
      await entry.locator('.tool-header').click()
      await entry.locator('summary').getByText('原始结果').waitFor()
      if (await entry.locator('.tool-result-image, .resource-list, .command-stderr').count()) throw new Error('无图片回执强行渲染结果')
      const visible = await entry.innerText()
      if (/原始网页观察正文|演示浏览器设备/.test(visible)) throw new Error('原始文字未折叠')
      await entry.locator('.tool-header').click()
    }
    for (const [index, output] of [[8, 'Finder, Chrome, Code'], [9, '标准错误：']]) {
      const command = host.locator(`[data-case="${index}"]`)
      await command.locator('.tool-header').click()
      if (!(await command.innerText()).includes(output)) throw new Error('设备命令输出未直接显示')
      if (await command.locator('details[open]').count()) throw new Error('命令原始回执默认展开')
    }
    if (await host.locator('details[open]').count()) throw new Error('原始结果默认展开')
    for (const dark of [false, true]) {
      await page.evaluate(dark => { window.__abridgeTheme.themeMode = dark ? 'dark' : 'light' }, dark)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        await page.locator('h2').hover()
        const normal = await opened.locator('.tool-status-icon svg').evaluate(svg => getComputedStyle(svg).color)
        await opened.locator('.tool-header').hover()
        const hover = await opened.locator('.tool-status-icon svg').evaluate(svg => getComputedStyle(svg).color)
        if (normal === hover) throw new Error('浏览器工具图标缺少统一 hover 反馈')
        await page.screenshot({ path: `/tmp/yuxi-abridge-browser-${dark ? 'dark' : 'light'}-${width}.png` })
        await host.locator('[data-case="8"]').screenshot({ path: `/tmp/yuxi-abridge-command-${dark ? 'dark' : 'light'}-${width}.png` })
        evidence.push({ dark, width, imagePreview: true, params: true, rawResultCollapsed: true, commandOutput: true, hover: true })
      }
    }
    return { cases: 10, evidence }
  } finally { await context.close() }
}
