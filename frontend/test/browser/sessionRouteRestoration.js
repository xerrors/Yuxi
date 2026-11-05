// 普通 member_session_id URL 恢复；延迟公开导航响应验证侧栏与路由最终状态。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/sessionRouteRestoration.js
// prettier-ignore
async page => {
  await page.unrouteAll({ behavior: 'wait' })
  const check = (ok, message) => { if (!ok) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const agent = { agent_id: 'restore-agent', name: '路由恢复智能体', can_run: true, config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const sessions = id => ({ id, object: 'agent.session', status: 'completed', agent: { id: agent.agent_id }, metadata: {}, created_at: 1793400000,
    yuxi: { title: id === 'restore-root' ? '根会话' : id, context: {}, config: {}, current_turn: null, project_id: null } })
  const delayed = new Map()
  await page.route('https://api.github.com/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('?')[0].replace(origin, '')
    let json = { data: [], files: [], attachments: [], skills: [], projects: [], providers: [], config: {} }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/discovery')) json = { capabilities: { features: { knowledge: false } } }
    if (path.endsWith('/auth/me')) json = { id: 'restore-user', uid: 'restore-user', username: '恢复测试', role: 'superadmin' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === '/api/agent/restore-agent') json = agent
    if (path === '/api/projects') json = []
    if (path === '/api/v1/agents/sessions') json = { data: [sessions('restore-root')], has_more: false }
    const session = path.match(/^\/api\/v1\/agents\/sessions\/([^/]+)$/)
    if (session) json = sessions(session[1])
    if (path.endsWith('/viewed')) json = sessions(path.split('/').at(-2))
    if (path.endsWith('/items')) json = { data: [], has_more: false, yuxi: { runs: [] } }
    if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    if (path.endsWith('/queue')) json = { inputs: [], status: 'ready', queue_paused: false }
    if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    if (path.endsWith('/feedback')) json = { items: [] }
    if (path.endsWith('/navigation')) {
      const id = path.split('/').at(-2)
      await new Promise(resolve => { delayed.set(id, resolve) })
      json = { session_id: id, name: id, tree_root_session_id: id === 'external' ? 'other-root' : 'restore-root', parent_session_id: 'restore-root' }
    }
    return route.fulfill({ json })
  })
  await page.addInitScript(() => { localStorage.setItem('user_token', 'restore-fixture'); localStorage.setItem('theme', 'light') })
  const navigate = path => page.evaluate(path => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(path), path)
  const requestMember = async id => {
    const requested = page.waitForRequest(request => request.url().endsWith('/' + id + '/navigation'))
    await navigate('/agent/restore-root?member_session_id=' + id)
    await requested
    while (!delayed.has(id)) await page.waitForTimeout(10)
  }
  try {
    await page.setViewportSize({ width: 1440, height: 960 })
    const firstRequested = page.waitForRequest(request => request.url().endsWith('/first/navigation'))
    await page.goto(origin + '/agent/restore-root?member_session_id=first')
    await firstRequested
    while (!delayed.has('first')) await page.waitForTimeout(10)
    await navigate('/agent/restore-root?member_session_id=latest')
    const latestRequested = page.waitForRequest(request => request.url().endsWith('/latest/navigation'))
    delayed.get('first')()
    await latestRequested
    while (!delayed.has('latest')) await page.waitForTimeout(10)
    check(await page.locator('.panel-session-chat:visible').count() === 0, '过期成员恢复不能先打开侧栏')
    delayed.get('latest')()
    await page.getByRole('tab', { name: 'latest', exact: true }).waitFor()
    check(page.url().split('?')[1] === 'member_session_id=latest', '恢复不能覆盖当前 member_session_id')
    check(await page.getByRole('tab', { name: 'first', exact: true }).count() === 0, '旧成员不得留下侧栏标签')

    await requestMember('closing')
    await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
    delayed.get('closing')()
    await page.waitForTimeout(300)
    check(await page.locator('.panel-session-chat:visible').count() === 0, '关面板后的迟到响应不能重新打开')

    await requestMember('clearing')
    await navigate('/agent/restore-root')
    delayed.get('clearing')()
    await page.waitForTimeout(300)
    check(await page.locator('.panel-session-chat:visible').count() === 0, '清除成员 URL 后不得重开')
    check(!page.url().includes('?'), '清除成员后不得恢复旧查询参数')

    await requestMember('external')
    delayed.get('external')()
    await page.getByText('加载对话失败: 会话不属于当前主页面', { exact: true }).waitFor()
    check(await page.locator('.panel-session-chat:visible').count() === 0, '外根成员不能在当前根打开')
    await page.screenshot({ path: '/tmp/yuxi-ordinary-route-rejected.png' })
    return { latestMemberOnly: true, memberQueryPreserved: true, closedPanelStaysClosed: true,
      clearedMemberStaysCleared: true, foreignRootRejected: true }
  } finally {
    for (const resolve of delayed.values()) resolve()
    await page.unrouteAll({ behavior: 'wait' })
  }
}
