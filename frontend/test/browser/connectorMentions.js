// 已登录超级管理员执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/connectorMentions.js
// 返回的临时用户和空会话使用后端测试清理工具回收；不执行占位远端连接器。
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const ownerToken = await page.evaluate(() => localStorage.getItem('user_token'))
  const owner = { Authorization: `Bearer ${ownerToken}` }
  const suffix = Date.now().toString(36)
  const slug = `pytest-browser-connector-${suffix}`
  const agent = `pytest-browser-mention-${suffix}`
  const screenshots = []
  let user, thread, userHeaders
  const request = async (path, options = {}) => {
    const response = await page.request.fetch(origin + path, { headers: owner, ...options })
    check(response.ok(), `测试准备或清理失败：${path} HTTP ${response.status()}`)
    return response.json()
  }
  try {
    await request('/api/system/mcp-servers', { method: 'POST', data: {
      slug, name: '会话连接器验收', description: '仅在当前会话选择，后续轮次继续使用。',
      url: 'https://example.com/mcp', transport: 'streamable_http', auth_mode: 'none'
    } })
    await page.goto(origin + '/extensions?tab=mcp')
    await page.getByPlaceholder('搜索连接器...').fill('会话连接器验收')
    await page.locator('main').getByText('会话连接器验收', { exact: true }).waitFor()
    check(await page.getByRole('button', { name: '新增连接器', exact: true }).count() === 1, '创建入口命名不一致')
    check(await page.getByRole('button', { name: '刷新连接器', exact: true }).count() === 1, '刷新按钮可访问名称不一致')
    await page.setViewportSize({ width: 1440, height: 900 })
    let path = '/tmp/yuxi-connectors-list.png'
    await page.locator('main').screenshot({ path })
    screenshots.push(path)
    await page.getByRole('button', { name: '新增连接器', exact: true }).click()
    const modal = page.locator('.mcp-form-modal .ant-modal-content')
    await modal.getByText('新增连接器', { exact: true }).waitFor()
    path = '/tmp/yuxi-connectors-create.png'
    await modal.screenshot({ path })
    screenshots.push(path)
    await page.keyboard.press('Escape')
    await request('/api/agent', { method: 'POST', data: {
      slug: agent, name: '连接器提及验收', backend_id: 'ChatbotAgent', visibility: 'shared',
      share_config: { version: 2, read_scope: { access_level: 'global' }, manage_scope: null },
      config_json: { context: { mcps: [], skills: [], knowledges: [], tools: [] } }
    } })
    const departments = await request('/api/departments')
    const password = `Fixture-${suffix}!`
    user = await request('/api/auth/users', { method: 'POST', data: {
      username: `pytest_conn_${suffix}`, password, role: 'user', department_id: departments[0].id
    } })
    const login = await request('/api/auth/token', { method: 'POST', form: { username: user.uid, password } })
    userHeaders = { Authorization: `Bearer ${login.access_token}` }
    thread = await request('/api/v1/agents/sessions', { method: 'POST', headers: { ...userHeaders, 'Idempotency-Key': slug + '-thread' },
      data: { agent_id: agent, title: '连接器提及验收' } })
    await page.evaluate(token => localStorage.setItem('user_token', token), login.access_token)
    await page.goto(origin + '/agent/' + thread.id)
    const editor = page.locator('.mention-editor[contenteditable="true"]:visible')
    await editor.waitFor()
    for (const dark of [false, true]) {
      await page.evaluate(async value => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(value ? 'dark' : 'light')
      }, dark)
      for (const width of [1440, 768]) {
        await page.setViewportSize({ width, height: 900 })
        await editor.fill('@会话连接器验收')
        await page.locator('.mention-group-title').getByText('连接器', { exact: true }).waitFor()
        const candidate = page.locator('.mention-item.resource-item').filter({ hasText: '会话连接器验收' })
        check(await candidate.count() === 1, 'Agent 未配置的可用连接器没有出现或重复')
        await page.locator('.mention-search-placeholder').filter({ hasText: '正在查找对话' }).waitFor({ state: 'hidden' })
        check(await page.locator('.mention-popup').evaluate(node => node.scrollWidth <= node.clientWidth), '连接器候选横向溢出')
        path = `/tmp/yuxi-connectors-mention-${dark ? 'dark' : 'light'}-${width}.png`
        await page.locator('main').screenshot({ path })
        screenshots.push(path)
      }
    }
    await editor.press('Enter')
    check(await editor.locator('[data-mention-raw]').getAttribute('data-mention-raw') === `@mcp:${slug}`, '键盘插入未保留 MCP 技术标识')
    await editor.press('Backspace')
    await editor.press('Backspace')
    check(await editor.locator('[data-mention-raw]').count() === 0, '连接器提及不能整体删除')
    await page.reload()
    await editor.waitFor()
    await editor.fill('@会话连接器验收')
    await page.locator('.mention-item.resource-item').filter({ hasText: '会话连接器验收' }).waitFor()
    await editor.press('Enter')
    await editor.locator('[data-mention-raw]').waitFor()
    let submitted
    await page.route(`**/api/v1/agents/sessions/${thread.id}/events`, async route => {
      submitted = route.request().postDataJSON()
      // 仅检查浏览器真实生成的请求；后端持久化和工具调用由真实 worker E2E 证明。
      await route.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ detail: {
        code: 'browser_fixture', message: '浏览器提交载荷已验证'
      } }) })
    })
    const response = page.waitForResponse(value => value.url().endsWith(`/sessions/${thread.id}/events`) && value.request().method() === 'POST')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await response
    check(JSON.stringify(submitted.events[0].yuxi.mcps) === JSON.stringify([slug]), '连接器选择没有随 Public 消息传递')
    check(submitted.events[0].input[0].content[0].text.includes(`@mcp:${slug}`), '原始提及正文丢失')
    await page.unroute(`**/api/v1/agents/sessions/${thread.id}/events`)
    return { passed: ['产品命名', '创建弹窗', 'Agent 范围外候选', '键盘插入与删除', '刷新后候选', 'Public 提交载荷', '明暗与 768/1440 布局'],
      screenshots, cleanup: { threadId: thread.id, userId: user.id, uid: user.uid } }
  } catch (error) {
    throw new Error(`${error.message}；清理标识：${JSON.stringify({ threadId: thread?.id, userId: user?.id })}`, { cause: error })
  } finally {
    if (thread) await request(`/api/v1/agents/sessions/${thread.id}/archive`, { method: 'POST', headers: userHeaders })
    await request('/api/agent/' + agent, { method: 'DELETE' })
    await request('/api/system/mcp-servers/' + slug, { method: 'DELETE' })
    await page.evaluate(token => localStorage.setItem('user_token', token), ownerToken)
  }
}
