// 真实组件展示 Abridge 浏览器合成回执，不连接设备或执行浏览器操作。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/abridgeBrowserRendering.js
// prettier-ignore
async originalPage => {
  const context = await originalPage.context().browser().newContext()
  const evidence = []
  try {
    const page = await context.newPage()
    await page.goto(originalPage.url().split('/').slice(0, 3).join('/') + '/login')
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
        { name: 'devices_list', args: {}, result: { devices: [{ id: 'browser-fixture', kind: 'browser', name: 'Chrome' }, { id: 'computer-fixture', kind: 'computer', platform: 'darwin', name: 'air' }] } },
        { name: 'session_open', args: { capabilities: ['browser'] }, result: { status: 'active', capabilities: ['browser'], device: { id: 'browser-fixture', kind: 'browser', name: 'Chrome' } } },
        { name: 'browser_open', args: { session_id: 'session-fixture', url: 'https://example.com/', screenshot: true }, result: JSON.stringify(mixed) },
        { name: 'browser_observe', args: { query: '导航' }, result: receipt },
        { name: 'browser_act', args: { action: { type: 'click', element_ref: 'ref-42' } }, result: receipt },
        { name: 'browser_act', args: { action: { type: 'fill', element_ref: 'ref-43', text: '示例输入' } }, result: mixed },
        { name: 'browser_open', args: { url: 'https://example.com/failure' }, result: [{ type: 'text', text: '{"status":"rejected","error":"未获得浏览器权限"}' }, mixed[1]] },
        { name: 'browser_act', args: {}, result: { status: 'outcome_unknown' } },
        { name: 'command_execute', args: { command: 'example-command' }, result: { status: 'executed', result: { stdout: 'Finder, Chrome, Code', exit_code: 0 } } },
        { name: 'command_execute', args: { command: 'failed-command' }, result: { status: 'executed', result: { stdout: '部分输出', stderr: '命令失败', exit_code: 1 } } },
        { name: 'session_close', args: { session_id: 'session-close-fixture' }, result: [{ type: 'text', text: '{"session_id":"session-close-fixture","status":"stopped"}' }] },
        { name: 'session_close', args: { session_id: 'session-failed-fixture' }, result: { status: 'failed', error: '无法关闭连接' } },
        { name: 'devices_list', args: {}, result: { devices: [{ id: 'long-name-fixture', kind: 'computer', name: '研发工作站设备长名称'.repeat(12) }] } },
        { name: 'session_open', args: { device_id: 'computer-fixture', capabilities: ['shell'] }, result: { status: 'active', capabilities: ['shell'], device: { id: 'computer-fixture', kind: 'computer', platform: 'darwin', name: 'air', capabilities: ['files', 'input', 'screen', 'shell'] } } },
        { name: 'files_list', args: { path: '/tmp/example' }, result: { status: 'executed', result: { path: '/tmp/example', entries: [{ name: 'note.txt', is_directory: false, size: 13 }, { name: 'folder', is_directory: true }, { name: 'shortcut', is_directory: false, is_symlink: true }], truncated: true } } },
        { name: 'file_write', args: { path: '/tmp/example/note.txt' }, result: { status: 'executed', result: { path: '/tmp/example/note.txt', size: 13, overwritten: true } } },
        { name: 'file_read', args: { path: '/tmp/example/note.txt' }, result: [{ type: 'text', text: JSON.stringify({ status: 'executed', result: { path: '/tmp/example/note.txt', content: '<script>hello</script>\n', size: 23, truncated: true } }) }] },
        { name: 'file_read', args: { path: '/tmp/example/empty.txt' }, result: { status: 'executed', result: { path: '/tmp/example/empty.txt', content: '', size: 0 } } },
        { name: 'files_list', args: { path: '/tmp/example/empty' }, result: { status: 'executed', result: { path: '/tmp/example/empty', entries: [] } } },
        { name: 'file_write', args: { path: '/tmp/example/rejected.txt' }, result: { status: 'rejected', error: '拒绝文件访问' } },
        { name: 'command_status', args: { operation_id: 'command-fixture', stdout_offset: 20 }, result: { status: 'executed', result: { stdout: 'Retained command output\n', stderr: '', exit_code: 0 }, output_truncated: true } },
        { name: 'command_status', args: { operation_id: 'command-fixture' }, result: { status: 'dispatched', output: { stdout: 'Partial command output', stderr: '' } } },
        { name: 'command_status', args: { operation_id: 'command-fixture' }, result: { status: 'executed', result: { stdout: 'Failed command output', stderr: 'Failure detail', exit_code: 1, timed_out: true } } },
        { name: 'command_status', args: { operation_id: 'command-fixture' }, result: { status: 'executed', result_available: false } },
        { name: 'web_search', args: { query: 'fixture' }, status: 'error', error_message: '搜索失败' },
        { name: 'unknown_tool', args: {}, status: 'error', error_message: '工具失败' },
        { name: 'files_list', args: { path: '/example' } },
        { name: 'files_list', args: { path: '/example' }, status: 'waiting' },
        { name: 'read_file', args: { file_path: '/skills/example/SKILL.md' }, result: '示例技能' },
        { name: 'unknown_tool', args: {}, result: '已完成' },
        { name: 'ask_user_question', args: { questions: [{ question: '是否继续？' }] }, result: { answer: '继续' } },
        { name: 'send_message', args: { name: '协作会话' }, status: 'error' }
      ]
      calls[14].result.result.entries.push({ name: 'very-long-device-filename-'.repeat(6) + '.txt', is_directory: false, size: 9999 })
      for (const [index, call] of calls.entries()) if (['files_list', 'file_write', 'file_read'].includes(call.name)) call.args.operation_id = `operation-file-fixture-${index}`
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
    const deviceHeader = host.locator('[data-case="0"] .tool-header')
    for (const name of ['Chrome', 'air']) await deviceHeader.getByText(name, { exact: true }).waitFor()
    if (!(await deviceHeader.innerText()).includes('2 台设备')) throw new Error('设备数量缺失')
    if (/浏览器|电脑/.test(await deviceHeader.innerText())) throw new Error('设备类型应由图标表达')
    await deviceHeader.locator('img').evaluateAll(async images => { if (images.length !== 2) throw new Error('设备平台图标缺失'); await Promise.all(images.map(image => image.decode())) })
    const connected = host.locator('[data-case="13"] .tool-header')
    await connected.getByText('air', { exact: true }).waitFor()
    await connected.getByText('命令执行', { exact: true }).waitFor()
    await connected.locator('img').evaluate(image => image.decode())
    if (/computer-fixture|文件访问|键鼠操作|屏幕查看/.test(await connected.innerText())) throw new Error('设备连接摘要混入 ID 或未连接能力')
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
      if (/原始网页观察正文|browser-fixture|computer-fixture/.test(visible)) throw new Error('原始文字未折叠')
      await entry.locator('.tool-header').click()
    }
    for (const [index, output] of [[8, 'Finder, Chrome, Code'], [9, '标准错误：']]) {
      const command = host.locator(`[data-case="${index}"]`)
      await command.locator('.tool-header').click()
      if (!(await command.innerText()).includes(output)) throw new Error('设备命令输出未直接显示')
      if (await command.locator('details[open]').count()) throw new Error('命令原始回执默认展开')
    }
    if (await host.locator('details[open]').count()) throw new Error('原始结果默认展开')
    const closed = host.locator('[data-case="10"]')
    await closed.getByText('断开设备连接', { exact: true }).waitFor()
    await closed.getByText('连接已关闭', { exact: true }).waitFor()
    if (await closed.locator('.lucide-unplug').count() !== 1) throw new Error('关闭连接缺少专用图标')
    await closed.locator('.tool-header').click()
    await closed.getByText('原始结果', { exact: true }).waitFor()
    if (await closed.locator('details[open]').count()) throw new Error('关闭连接原始回执默认展开')
    const failed = host.locator('[data-case="11"]')
    await failed.getByText('执行失败', { exact: true }).waitFor()
    if ((await failed.innerText()).includes('连接已关闭')) throw new Error('关闭失败被展示为关闭成功')
    for (const [index, expected] of [[14, ['note.txt', 'folder', 'shortcut*', '目录列表已截断']], [15, ['写入成功', '13 B', '已覆盖']], [16, ['<script>hello</script>', '内容已截断']], [17, ['文件为空']], [18, ['目录为空']]]) {
      const entry = host.locator(`[data-case="${index}"]`)
      await entry.locator('.tool-header').click()
      for (const text of expected) if (!(await entry.innerText()).includes(text)) throw new Error(`设备文件工具缺少可读结果：${index}/${text}`)
      if ((await entry.locator('.tool-header').innerText()).includes('operation-file-fixture')) throw new Error('文件工具 header 泄露操作标识')
      const typography = await entry.locator('.device-file-summary, .device-file-list').first().evaluate(node => ({ size: getComputedStyle(node).fontSize, lineHeight: getComputedStyle(node).lineHeight }))
      if (typography.size !== '12px' || typography.lineHeight !== '20px') throw new Error(`设备文件结果字号不一致：${JSON.stringify(typography)}`)
      if (await entry.locator('details[open]').count()) throw new Error('设备文件原始结果默认展开')
      if (await entry.locator('a[href^="/tmp"], .device-file-content script').count()) throw new Error('设备文件路径或正文被作为本地链接或HTML执行')
    }
    await host.locator('[data-case="19"]').getByText('执行失败', { exact: true }).waitFor()
    if ((await host.locator('[data-case="19"]').innerText()).includes('写入成功')) throw new Error('文件写入拒绝被显示为成功')
    for (const [index, expected] of [[20, ['Retained command output', '输出已截断']], [21, ['Partial command output', '命令处理中']], [22, ['Failed command output', 'Failure detail', '命令执行超时', '执行失败']], [23, ['命令输出已过期']]]) {
      const command = host.locator(`[data-case="${index}"]`)
      await command.getByText('查看命令输出', { exact: true }).waitFor()
      await command.locator('.tool-header').click()
      for (const text of expected) if (!(await command.innerText()).includes(text)) throw new Error(`命令查询缺少结果：${text}；实际：${await command.innerText()}`)
      if (await command.locator('details[open]').count()) throw new Error('命令查询原始结果默认展开')
    }
    for (const [index, icon] of [[24, 'globe'], [25, 'wrench']]) {
      await host.locator(`[data-case="${index}"] .tool-status-icon--error .lucide-${icon}`).waitFor()
      if (await host.locator(`[data-case="${index}"] .lucide-circle-x`).count()) throw new Error('失败工具仍使用叉号')
    }
    for (const dark of [false, true]) {
      await page.evaluate(dark => { window.__abridgeTheme.themeMode = dark ? 'dark' : 'light' }, dark)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        const colors = await host.evaluate(node => {
          const probe = document.createElement('span')
          node.append(probe)
          probe.style.color = 'var(--gray-600)'
          const normal = getComputedStyle(probe).color
          probe.style.color = 'var(--gray-800)'
          const active = getComputedStyle(probe).color
          probe.remove()
          return { normal, active }
        })
        await opened.locator('.tool-header').click()
        await host.locator('h2').hover()
        const normal = await opened.locator('.tool-status-icon svg').evaluate(svg => getComputedStyle(svg).color)
        if (normal !== colors.normal) throw new Error('收起的工具 header 未使用 gray-600')
        await opened.locator('.tool-header').hover()
        const hover = await opened.locator('.tool-status-icon svg').evaluate(svg => getComputedStyle(svg).color)
        if (hover !== colors.active) throw new Error('悬停的工具 header 未使用 gray-800')
        await opened.locator('.tool-header').click()
        await host.locator('h2').hover()
        const expanded = await opened.locator('.tool-status-icon svg').evaluate(svg => getComputedStyle(svg).color)
        if (expanded !== colors.active) throw new Error('展开的工具 header 未使用 gray-800')
        await opened.locator('.tool-header').focus()
        const focused = await opened.locator('.tool-status-icon svg').evaluate(svg => getComputedStyle(svg).color)
        if (expanded !== focused) throw new Error('工具图标在 focus 时额外变色')
        const mismatched = await host.locator('.tool-header, .cooperation-event-row').evaluateAll(headers => headers.flatMap(header => {
          const color = getComputedStyle(header).color
          return [...header.querySelectorAll('*')].filter(node => getComputedStyle(node).color !== color)
            .map(node => ({ tag: node.tagName, class: node.getAttribute('class') }))
        }))
        if (mismatched.length) throw new Error(`工具 header 子元素未继承统一颜色：${JSON.stringify(mismatched)}`)
        await page.screenshot({ path: `/tmp/yuxi-abridge-browser-${dark ? 'dark' : 'light'}-${width}.png` })
        await host.locator('[data-case="8"]').screenshot({ path: `/tmp/yuxi-abridge-command-${dark ? 'dark' : 'light'}-${width}.png` })
        await closed.screenshot({ path: `/tmp/yuxi-abridge-session-close-${dark ? 'dark' : 'light'}-${width}.png` })
        await host.locator('[data-case="0"]').screenshot({ path: `/tmp/yuxi-abridge-devices-header-${dark ? 'dark' : 'light'}-${width}.png` })
        await connected.screenshot({ path: `/tmp/yuxi-abridge-session-device-${dark ? 'dark' : 'light'}-${width}.png` })
        const directory = host.locator('[data-case="14"] .device-file-list')
        if (await directory.locator('img, svg').count() || (await directory.innerText()).includes('符号链接')) throw new Error('目录仍包含图标或完整符号链接标注')
        if ((await directory.innerText()).includes('13 B')) throw new Error('目录仍展示文件大小')
        const layout = await directory.evaluate(list => {
          const rows = [...list.children].map(node => node.getBoundingClientRect())
          return { first: rows[0].top, second: rows[1].top, last: rows.at(-1).top, overflow: list.scrollWidth > list.clientWidth }
        })
        if (layout.first !== layout.second || layout.last <= layout.first || layout.overflow) throw new Error('目录条目未正确行内排列和换行')
        for (const index of [14, 15, 16]) await host.locator(`[data-case="${index}"]`).screenshot({ path: `/tmp/yuxi-abridge-files-${index}-${dark ? 'dark' : 'light'}-${width}.png` })
        await host.locator('[data-case="20"]').screenshot({ path: `/tmp/yuxi-command-status-${dark ? 'dark' : 'light'}-${width}.png` })
        await host.locator('h2').hover()
        for (const index of [9, 24, 25]) {
          const row = host.locator(`[data-case="${index}"]`)
          const errorColor = await row.locator('.tool-status-icon svg').evaluate(node => getComputedStyle(node).color)
          const isExpanded = await row.locator('.tool-header').getAttribute('aria-expanded') === 'true'
          if (errorColor !== (isExpanded ? colors.active : colors.normal)) throw new Error('失败工具 header 未遵循展开/收起颜色')
        }
        const longName = host.locator('[data-case="12"] .device-name')
        if (!(await host.locator('[data-case="12"] .description').getAttribute('title')).includes('研发工作站设备长名称'.repeat(12))) throw new Error('长设备名称缺少完整提示')
        if (!await longName.evaluate(node => getComputedStyle(node).textOverflow === 'ellipsis' && node.scrollWidth > node.clientWidth)) throw new Error('长设备名称没有省略显示')
        evidence.push({ dark, width, imagePreview: true, params: true, rawResultCollapsed: true, commandOutput: true, inheritedHeaderColor: true })
      }
    }
    return { cases: 32, evidence }
  } finally { await context.close() }
}
