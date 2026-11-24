// 真实会话页面，HTTP 边界使用合成设备与会话数据。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/deviceMentions.js
// prettier-ignore
async originalPage => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const context = await originalPage.context().browser().newContext()
  try {
    const page = await context.newPage()
    const origin = originalPage.url().split('/').slice(0, 3).join('/')
    const agent = { agent_id: 'device-demo', name: '设备演示助手', can_run: true,
      config_json: { context: { model: 'fixture:chat' } }, configurable_items: {}, metadata: {} }
    const thread = { id: 'device-thread', object: 'agent.session', status: 'completed',
      agent: { id: agent.agent_id }, created_at: 1793400000, metadata: {},
      yuxi: { title: '设备引用演示', context: {}, config: {}, project_id: null, current_turn: null } }
    const sessionPath = '/api/v1/agents/sessions'
    const eventPath = `${sessionPath}/${thread.id}/events`
    const devices = [
      { id: 'dev-a', name: '会议室浏览器', kind: 'browser', status: 'online' },
      { id: 'dev-b', name: '会议室浏览器', kind: 'browser', status: 'online' },
      { id: 'dev-workstation', name: '研发协作与发布流程演示用测试工作站', kind: 'computer', status: 'online' }
    ]
    const submissions = []
    let deviceReads = 0, deviceMode = 'loading', releaseDevices
    const holdDevices = () => new Promise(resolve => { releaseDevices = resolve })
    let waitForDevices = holdDevices()
    const screenshots = []
    const screenshot = async name => {
      const path = `/tmp/yuxi-device-mentions-${name}.png`
      await page.screenshot({ path, animations: 'disabled' })
      screenshots.push(path)
    }

    await context.addInitScript(() => {
      localStorage.setItem('user_token', 'synthetic-browser-test')
      localStorage.setItem('theme', 'light')
    })
    await context.route('**/*', async route => {
      const request = route.request()
      if (request.url().startsWith('https://api.github.com/')) return route.fulfill({ json: {} })
      if (!request.url().startsWith(origin + '/')) return route.abort()
      const path = request.url().slice(origin.length).split('?')[0]
      if (!path.startsWith('/api/')) return route.continue()

      let json = { data: [], files: [], attachments: [], skills: [], projects: [], providers: [], config: {} }
      if (path === '/api/devices') {
        check(request.method() === 'GET', '候选应读取设备列表')
        deviceReads++
        if (deviceMode === 'loading') await waitForDevices
        if (deviceMode === 'error') return route.fulfill({ status: 503, json: { detail: '演示服务不可用' } })
        json = { state: deviceMode === 'login_required' ? deviceMode : 'ready', configured: true,
          management_url: 'https://bridge.example/api/auth/oidc/login', devices: deviceMode === 'ready' ? devices : [] }
      } else if (path === '/api/system/info') json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
      else if (path === '/api/auth/me') json = { id: 1, uid: 'demo-user', username: '演示用户', role: 'user' }
      else if (path.endsWith('/check-first-run')) json = { first_run: false }
      else if (path.endsWith('/discovery')) json = { capabilities: { features: { knowledge: false } } }
      else if (path === '/api/system/config') json = { default_model: 'fixture:chat' }
      else if (path === '/api/agent') json = { agents: [agent] }
      else if (path === `/api/agent/${agent.agent_id}`) json = agent
      else if (path === '/api/projects' || path.startsWith('/api/mention/')) json = []
      else if (path === sessionPath) json = { data: [thread], has_more: false }
      else if (path === `${sessionPath}/${thread.id}` || path.endsWith('/viewed')) json = thread
      else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
      else if (path.endsWith('/queue')) json = { inputs: [], status: 'ready', queue_paused: false }
      else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
      else if (path.endsWith('/items')) json = { has_more: false, yuxi: { runs: [] }, data: submissions.map((event, index) => ({
        id: `device-message-${index}`, object: 'agent.session.item', type: 'message', role: 'user', status: 'completed',
        turn_id: 'device-turn', content: event.input[0].content,
        yuxi: { message_id: `device-message-${index}`, created_at: '2026-11-24T00:00:00Z' }
      })) }
      else if (path === eventPath) {
        if (request.method() === 'GET') return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
        submissions.push(request.postDataJSON().events[0])
        return route.fulfill({ status: 202, json: { input_id: `device-input-${submissions.length}`, status: 'pending' } })
      } else if (path.includes('/inputs/')) json = { input_id: path.split('/').at(-1), status: 'pending' }
      return route.fulfill({ json })
    })

    const editor = page.locator('.agent-session-workspace .mention-editor[contenteditable="true"]')
    const popup = page.locator('.mention-popup')
    const group = popup.locator('.mention-group').filter({ has: page.getByText('设备', { exact: true }) })
    const rows = group.locator('.mention-item')
    const activeId = group.locator('.mention-item.active .resource-description')
    const chip = editor.locator('[data-mention-raw]')
    const send = page.getByRole('button', { name: '发送消息', exact: true })
    const submit = async expected => {
      const posted = page.waitForRequest(request => request.method() === 'POST' && request.url() === origin + eventPath)
      await send.click()
      const event = (await posted).postDataJSON().events[0]
      check(event.type === 'agent.session.input.message' && event.yuxi.mode === 'follow_up', '设备引用改变了普通输入协议')
      check(JSON.stringify(event.input) === JSON.stringify([{ role: 'user', content: [{ type: 'input_text', text: expected }] }]), '请求正文与选中的设备不一致')
      await page.waitForFunction(() => document.querySelector('.mention-editor')?.textContent === '')
    }

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/agent/${thread.id}`)
    await editor.waitFor()
    check(deviceReads === 0, '打开会话不应提前读取设备')
    try {
      await editor.fill('请查看 @')
      await group.getByText('正在加载设备…', { exact: true }).waitFor()
      await popup.getByText('暂无可引用的对话', { exact: true }).waitFor()
      await screenshot('loading-light-1440')
    } finally {
      deviceMode = 'ready'
      releaseDevices()
    }
    await rows.nth(2).waitFor()
    await page.keyboard.insertText('会议室')
    check(await rows.count() === 2 && deviceReads === 1, '按名称筛选时丢失同名设备或重新请求了设备列表')
    check((await rows.locator('.resource-name').allTextContents()).every(name => name === '会议室浏览器'), '候选未显示设备名称')
    check(JSON.stringify(await rows.locator('.resource-description').allTextContents()) === JSON.stringify(['dev-a', 'dev-b']), '同名候选未以独立 ID 区分')
    await activeId.getByText('dev-a', { exact: true }).waitFor()
    await editor.press('ArrowDown')
    await activeId.getByText('dev-b', { exact: true }).waitFor()
    await popup.getByText('未找到标题匹配的对话，试试其他关键词', { exact: true }).waitFor()
    await screenshot('candidates-light-1440')
    await editor.press('Enter')
    await chip.getByText('会议室浏览器', { exact: true }).waitFor()
    check(await chip.getAttribute('data-mention-raw') === '@device:"dev-b 会议室浏览器"', '键盘选中的 ID 没有保存在草稿')
    check(submissions.length === 0, '选择候选时不应发送消息')
    await page.keyboard.insertText('的状态')
    await submit('请查看 @device:"dev-b 会议室浏览器" 的状态')

    await page.reload()
    const historicalChip = page.locator('.message-box.human .mention-ref-device').getByText('会议室浏览器', { exact: true })
    await historicalChip.waitFor()
    check(deviceReads === 1, '回读消息不应依赖重新获取候选')
    await screenshot('sent-light-1440')

    await editor.fill('@')
    await rows.nth(2).waitFor()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(async mode => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(mode)
      }, theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 900 })
        const rect = await popup.boundingBox()
        check(rect && rect.x >= 0 && rect.x + rect.width <= width && await popup.evaluate(node => node.scrollWidth <= node.clientWidth + 1), `候选横向溢出：${theme}/${width}`)
        if (theme === 'dark' && width === 768) await screenshot('candidates-dark-768')
      }
    }
    await page.keyboard.insertText('dev-workstation')
    check(await rows.count() === 1 && deviceReads === 2, '按 ID 筛选时未复用本次打开的设备列表')
    await popup.getByText('未找到标题匹配的对话，试试其他关键词', { exact: true }).waitFor()
    await rows.first().click()
    check(await chip.getAttribute('data-mention-raw') === '@device:"dev-workstation 研发协作与发布流程演示用测试工作站"', '鼠标没有插入对应设备')

    deviceMode = 'error'
    await editor.fill('@')
    await group.getByText('设备加载失败，重新打开可重试', { exact: true }).waitFor()
    check(await rows.count() === 0, '加载失败时仍显示过期设备')
    await popup.getByText('暂无可引用的对话', { exact: true }).waitFor()
    await screenshot('error-dark-768')
    await editor.press('Escape')
    deviceMode = 'ready'
    await editor.fill('@会议室')
    await rows.nth(1).waitFor()
    await editor.press('Escape')

    deviceMode = 'login_required'
    await editor.fill('@')
    await group.getByText('请从设备管理进入 aBridge 并完成授权', { exact: true }).waitFor()
    check(await rows.count() === 0, '需要授权时显示了设备候选')
    await editor.press('Escape')

    deviceMode = 'empty'
    const emptyResponse = page.waitForResponse(response => response.url() === origin + '/api/devices')
    await editor.fill('@')
    await emptyResponse
    await group.waitFor({ state: 'hidden' })
    check(await popup.isVisible(), '无设备时应保留其他提及分组')
    await editor.fill('继续发送普通消息')
    await submit('继续发送普通消息')

    const deviceUrl = origin + '/api/devices'
    deviceMode = 'loading'
    waitForDevices = holdDevices()
    const previousReads = deviceReads
    try {
      const pending = page.waitForRequest(deviceUrl)
      await editor.fill('@')
      await pending
      const abort = page.waitForEvent('requestfailed', request => request.url() === deviceUrl)
      await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/workspace'))
      await page.waitForURL('**/workspace')
      await abort
    } finally {
      deviceMode = 'ready'
      releaseDevices()
    }
    await page.goBack()
    await page.waitForURL(`**/agent/${thread.id}`)
    await editor.waitFor()
    check(await popup.count() === 0 && deviceReads === previousReads + 1, '返回保活会话时恢复了旧菜单或读取了设备')
    await editor.fill('@dev-b')
    await rows.getByText('dev-b', { exact: true }).waitFor()
    check(deviceReads === previousReads + 2, '返回后打开菜单没有重新获取设备')
    return { submissions: submissions.map(event => event.input[0].content[0].text), deviceReads, screenshots }
  } finally {
    await context.close()
  }
}
