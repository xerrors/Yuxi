// 固定公开协议数据验证真实页面重排；不调用模型或写入业务记录。
// playwright-cli -s=reading-ui run-code --filename=frontend/test/browser/chatReadingPosition.js
// prettier-ignore
async page => {
  await page.unrouteAll({ behavior: 'wait' })
  const check = (ok, message) => { if (!ok) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const agent = { agent_id: 'reading-agent', name: '阅读测试智能体', can_run: true, is_default: true, config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const thread = { id: 'reading-thread', object: 'agent.session', status: 'completed', agent: { id: agent.agent_id }, metadata: {}, created_at: 1793400000, yuxi: { title: '对话阅读位置验证', context: {}, config: {}, current_turn: null, project_id: null } }
  const items = Array.from({ length: 24 }, (_, i) => ({
    id: `reading-message-${i}`, object: 'agent.session.item', type: 'message', role: 'user', status: 'completed', turn_id: `reading-turn-${i}`,
    content: [{ type: 'input_text', text: `阅读段落 ${i + 1}：` + '宽度改变后，正在阅读的这行文字应该留在视窗内。我们用长段落验证自动换行，不依赖固定像素或滚动百分比。'.repeat(55) }],
    yuxi: { message_id: `reading-message-${i}`, run_id: `reading-run-${i}`, created_at: new Date(1793400000000 + i * 1000).toISOString() }
  }))
  await page.route('https://api.github.com/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('?')[0].replace(origin, '')
    let json = { data: [], files: [], attachments: [], skills: [], projects: [], providers: [], config: {} }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/discovery')) json = { capabilities: { features: { knowledge: false } } }
    if (path.endsWith('/auth/me')) json = { id: 'reading-user', uid: 'reading-user', username: '阅读测试', role: 'superadmin' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path === '/api/user/config') json = { enable_memory: true }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === '/api/agent/reading-agent') json = agent
    if (path === '/api/projects') json = []
    if (path === '/api/v1/agents/sessions') json = { data: [thread], has_more: false }
    if (path.endsWith('/reading-thread') || path.endsWith('/viewed')) json = thread
    if (path.endsWith('/navigation')) json = { session_id: thread.id, name: thread.yuxi.title, tree_root_session_id: thread.id, parent_session_id: null }
    if (path.endsWith('/items')) json = { data: items, has_more: false, yuxi: { runs: items.map((item, i) => ({ id: item.yuxi.run_id, turn_id: item.turn_id, status: 'completed', artifacts: [], created_at: new Date(1793400000000 + i * 1000).toISOString() })) } }
    if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    if (path.endsWith('/queue')) json = { inputs: [], status: 'completed', queue_paused: false }
    if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    if (path.endsWith('/feedback')) json = { items: [] }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
    return route.fulfill({ json })
  })
  await page.addInitScript(() => {
    localStorage.setItem('user_token', 'reading-fixture')
    localStorage.setItem('theme', 'light')
    localStorage.setItem('chat-ui-store', JSON.stringify({ sidebarCollapsed: false }))
  })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.goto(origin + '/agent/reading-thread')
  let chat = page.locator('.agent-view > .agent-session-workspace > .chat > .chat-content-container > .chat-main')
  await chat.locator('.history-display-item').nth(12).waitFor()
  await page.waitForTimeout(400) // 等待首次历史定位和布局过渡结束。
  /** 使用独立字符作为 oracle，记录同一文字重排前后的视窗坐标。 */
  const placeAnchor = async () => {
    await chat.evaluate(el => {
      const message = el.querySelectorAll('.history-display-item')[12]
      const walker = document.createTreeWalker(message, NodeFilter.SHOW_TEXT)
      let node
      while ((node = walker.nextNode()) && node.length < 1800) { /* 找长段落正文 */ }
      if (!node) throw new Error('缺少长段落测试文本')
      const range = document.createRange()
      range.setStart(node, 1700)
      range.setEnd(node, 1701)
      el.scrollTop += range.getBoundingClientRect().top - el.getBoundingClientRect().top - 5
      window.__readingOracle = range
    })
    await page.waitForTimeout(120)
    return readAnchor()
  }
  const readAnchor = () => chat.evaluate(el => window.__readingOracle.getBoundingClientRect().top - el.getBoundingClientRect().top)
  const assertAnchor = async (top, label) => {
    await page.waitForTimeout(400) // 宽度过渡期间每帧均重新布局，检查最终坐标。
    const next = await readAnchor()
    check(Math.abs(next - top) <= 26, `${label}：同一字符偏移 ${next - top}px`)
  }
  const top = await placeAnchor()
  for (const width of [1024, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 })
    await assertAnchor(top, `视口 ${width}`)
  }
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  await assertAnchor(top, '收起主导航')
  await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  await assertAnchor(top, '展开主导航')
  await page.getByRole('button', { name: '打开侧边栏', exact: true }).click()
  await assertAnchor(top, '打开右侧面板')
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  await assertAnchor(top, '关闭右侧面板')
  await chat.evaluate(el => { el.scrollTop = el.scrollHeight })
  await page.waitForTimeout(150)
  for (const width of [768, 1440]) {
    await page.setViewportSize({ width, height: 800 })
    await page.waitForTimeout(400)
    const gap = await chat.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)
    check(gap <= 2, `底部跟随失效：${gap}px`)
  }
  const userTop = await placeAnchor()
  await page.setViewportSize({ width: 1024, height: 960 })
  await assertAnchor(userTop, '手动上滚后保持阅读')
  check(await chat.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight > 1000), '重排把用户拉回底部')
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  for (const label of ['对话', '智能体', '个人空间']) {
    const link = page.getByRole('link', { name: label, exact: true })
    check(await link.getAttribute('aria-label') === label, '折叠导航缺少稳定名称：' + label)
    check(await link.locator('svg[aria-hidden="true"]').count() > 0, '装饰导航图标未隐藏：' + label)
  }
  for (const theme of ['light', 'dark']) {
    await page.evaluate(async theme => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(theme) }, theme)
    await page.waitForTimeout(250)
    await page.screenshot({ path: '/tmp/yuxi-ordinary-reading-' + theme + '.png' })
  }
  await page.setViewportSize({ width: 768, height: 960 })
  await page.waitForTimeout(400)
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '768px 出现横向溢出')
  await page.screenshot({ path: '/tmp/yuxi-ordinary-reading-768.png' })
  await page.unrouteAll({ behavior: 'wait' })
  return { readingPosition: true, bottomFollow: true, manualScroll: true, navigationNames: true, widths: [1440, 1024, 768], themes: ['light', 'dark'] }
}
