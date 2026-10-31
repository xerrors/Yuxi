// 固定公开协议数据验证真实页面重排；不调用模型或写入业务记录。
// playwright-cli -s=reading-ui run-code --filename=frontend/test/browser/chatReadingPosition.js
// prettier-ignore
async page => {
  await page.unrouteAll({ behavior: 'wait' })
  const check = (ok, message) => { if (!ok) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const agent = { agent_id: 'reading-agent', name: '阅读测试助手', can_run: true, is_default: true, config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const thread = { id: 'reading-thread', object: 'agent.session', status: 'completed', agent: { id: agent.agent_id }, metadata: {}, created_at: 1793400000, yuxi: { title: '对话阅读位置验证', context: {}, config: {}, current_turn: null, project_id: null } }
  const items = Array.from({ length: 24 }, (_, i) => ({
    id: `reading-message-${i}`, object: 'agent.session.item', type: 'message', role: 'user', status: 'completed', turn_id: `reading-turn-${i}`,
    content: [{ type: 'input_text', text: `阅读段落 ${i + 1}：` + '宽度改变后，正在阅读的这行文字应该留在视窗内。我们用长段落验证自动换行，不依赖固定像素或滚动百分比。'.repeat(55) }],
    yuxi: { message_id: `reading-message-${i}`, run_id: `reading-run-${i}`, created_at: new Date(1793400000000 + i * 1000).toISOString() }
  }))
  let personal = false
  let mentionSessionReads = 0
  await page.route('https://api.github.com/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('?')[0].replace(origin, '')
    let json = { data: [], files: [], attachments: [], skills: [], projects: [], providers: [], config: {} }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/discovery')) json = { capabilities: { features: { personal_assistant: true } } }
    if (path.endsWith('/auth/me')) json = { id: 'reading-user', uid: 'reading-user', username: '阅读测试', role: 'superadmin' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === '/api/agent/reading-agent') json = { ...agent, is_personal_assistant: personal }
    if (path === '/api/projects') json = []
    if (path === '/api/mention/sessions') {
      mentionSessionReads++
      json = [{ thread_id: 'other-thread', title: '普通对话标题' }]
    }
    if (path === '/api/agents/personal') json = { assistant: { id: 'reading-assistant', agent_id: agent.agent_id, thread_id: thread.id, name: '小析', icon: '/icons/personal-assistant/orbit.svg' }, model_available: true }
    if (path === '/api/agents/personal/sessions') json = { sessions: [
      { session_id: 'reading-task-1', title: '整理本周研究笔记', turn_status: 'running' },
      { session_id: 'reading-task-2', title: '核对实验结果和参考资料', turn_status: 'pending' }
    ] }
    if (path === '/api/v1/agents/sessions') json = { data: personal ? [] : [thread], has_more: false }
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
  personal = true
  await page.goto(origin + '/assistant')
  await page.getByRole('textbox', { name: '向你的助理发送消息', exact: true }).waitFor()
  const beforeAddMenu = mentionSessionReads
  await page.getByRole('button', { name: '添加内容', exact: true }).click()
  await page.getByText('上传附件', { exact: true }).waitFor()
  await page.getByText('添加图片', { exact: true }).waitFor()
  await page.waitForTimeout(350) // 超过远程候选查询防抖时间。
  check(await page.locator('.session-mention-group').count() === 0, '个人助理加号不应显示对话引用')
  check(mentionSessionReads === beforeAddMenu, '个人助理上传菜单不应查询对话候选')
  await page.keyboard.press('Escape')
  chat = page.locator('.personal-workspace > .chat > .chat-content-container > .chat-main')
  await chat.locator('.history-display-item').nth(12).waitFor()
  await page.waitForTimeout(400)
  const personalTop = await placeAnchor()
  for (const width of [1440, 768, 1024]) {
    await page.setViewportSize({ width, height: 960 })
    await assertAnchor(personalTop, `个人助理 ${width}`)
  }
  const links = await page.locator('.nav .nav-item').allTextContents()
  check(links.slice(0, 3).map(x => x.trim()).join(',') === '对话,个人助理,智能体', `导航顺序错误：${links}`)
  check(await page.locator('.foo .personal-assistant-nav').count() === 0, '底部仍有重复助理入口')
  await page.getByRole('button', { name: '查看助理完整轨迹', exact: true }).click()
  await page.locator('.panel-session-chat:visible').waitFor()
  chat = page.locator('.panel-session-chat:visible .chat-main')
  await chat.locator('.history-display-item').nth(12).waitFor()
  await page.waitForTimeout(400)
  const traceTop = await placeAnchor()
  await page.setViewportSize({ width: 1440, height: 960 })
  await assertAnchor(traceTop, '右侧轨迹宽度改变')
  check(await page.getByRole('tab', { name: '文件', exact: true }).count() === 0, '助理侧栏仍有文件标签')
  await page.getByRole('button', { name: '关闭 对话阅读位置验证', exact: true }).click()
  check(await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).count() === 0, '关闭最后轨迹后留下空面板')
  chat = page.locator('.personal-workspace > .chat > .chat-content-container > .chat-main')
  const beforeProfile = await placeAnchor()
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  const profile = page.getByRole('dialog', { name: '个人助理资料', exact: true })
  await profile.waitFor()
  await page.waitForTimeout(350)
  check(await profile.evaluate(el => !!el.closest('.personal-profile-dock')), '宽屏资料卡片仍在悬浮')
  await assertAnchor(beforeProfile, '平铺卡片打开后保持阅读')
  const mainChat = page.locator('.personal-workspace > .chat > .chat-content-container > .chat-main')
  const checkProfileLayout = async embedded => {
    const card = await profile.boundingBox()
    const trigger = await page.getByRole('button', { name: '个人助理资料', exact: true }).boundingBox()
    check(card.y >= trigger.y + trigger.height + 11, '资料卡片与页头按钮间距不足12px')
    if (embedded) {
      const box = await mainChat.locator(':scope > .chat-box').boundingBox()
      const input = await mainChat.locator(':scope > .bottom').boundingBox()
      check(box.x + box.width <= card.x && input.x + input.width <= card.x, '平铺卡片遮挡正文或输入框')
    } else {
      check(Math.abs(card.x + card.width - trigger.x - trigger.width) <= 1, '悬浮卡片未与头像按钮右对齐')
    }
  }
  await checkProfileLayout(true)
  await mainChat.click({ position: { x: 20, y: 120 } })
  check(await profile.isVisible(), '平铺卡片不应因点击正文关闭')
  await profile.getByRole('button', { name: '编辑资料', exact: true }).focus()
  await page.screenshot({ path: '/tmp/yuxi-profile-embedded-light.png' })
  await page.setViewportSize({ width: 1024, height: 960 })
  await page.waitForTimeout(400)
  check(await profile.evaluate(el => el.classList.contains('is-floating')), '窄屏卡片未改为悬浮')
  await assertAnchor(beforeProfile, '卡片由平铺切到悬浮后保持阅读')
  await checkProfileLayout(false)
  check(await profile.getByRole('button', { name: '编辑资料', exact: true }).evaluate(el => el === document.activeElement), '卡片切换位置后丢失原有键盘焦点')
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.waitForTimeout(400)
  check(await profile.getByRole('button', { name: '编辑资料', exact: true }).evaluate(el => el === document.activeElement), '悬浮转平铺后丢失原有键盘焦点')
  await page.setViewportSize({ width: 1024, height: 960 })
  await page.waitForTimeout(400)
  const composer = mainChat.getByRole('textbox', { name: '向你的助理发送消息', exact: true })
  await composer.focus()
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.waitForTimeout(400)
  check(await composer.evaluate(el => el === document.activeElement), '平铺卡片抢走正文输入焦点')
  await page.setViewportSize({ width: 1024, height: 960 })
  await page.waitForTimeout(400)
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('dark') })
  await page.screenshot({ path: '/tmp/yuxi-profile-floating-dark.png' })
  await page.setViewportSize({ width: 1024, height: 320 })
  await page.waitForTimeout(350)
  const shortCard = await profile.boundingBox()
  check(shortCard.y + shortCard.height <= 320, '低高度视窗资料卡片越界')
  check(await profile.evaluate(el => { el.scrollTop = el.scrollHeight; return el.scrollTop > 0 }), '低高度资料卡片无法滚动查看完整内容')
  await page.setViewportSize({ width: 1024, height: 960 })
  await page.waitForTimeout(350)
  await page.keyboard.press('Escape')
  await profile.waitFor({ state: 'hidden' })
  check(await page.getByRole('button', { name: '个人助理资料', exact: true }).evaluate(el => el === document.activeElement), 'Escape后焦点未返回头像按钮')
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  await profile.waitFor()
  await page.mouse.click(260, 500)
  await profile.waitFor({ state: 'hidden' })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.getByRole('button', { name: '查看助理完整轨迹', exact: true }).click()
  await page.locator('.panel-session-chat:visible').waitFor()
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  await profile.waitFor()
  await page.waitForTimeout(400)
  check(await profile.evaluate(el => el.classList.contains('is-floating')), '打开轨迹后主对话不足宽度仍错误平铺')
  await checkProfileLayout(false)
  await page.keyboard.press('Escape')
  for (const width of [800, 768]) {
    await page.setViewportSize({ width, height: 960 })
    await page.waitForTimeout(400)
    await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
    await profile.waitFor()
    await checkProfileLayout(false)
    const card = await profile.boundingBox()
    const navigation = await page.locator('.header').boundingBox()
    check(card.x >= navigation.x + navigation.width, `${width}px卡片覆盖主导航`)
    await page.keyboard.press('Escape')
  }
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: '个人助理资料', exact: true }).click()
  await page.waitForTimeout(350)
  check(await profile.evaluate(el => !!el.closest('.personal-profile-dock')), '关闭轨迹后未恢复平铺')
  await page.screenshot({ path: '/tmp/yuxi-profile-embedded-dark.png' })
  await page.getByRole('button', { name: '编辑资料', exact: true }).click()
  await page.getByRole('dialog', { name: '编辑个人助理', exact: true }).waitFor()
  await page.waitForTimeout(400)
  for (const theme of ['light', 'dark']) {
    await page.evaluate(async theme => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(theme) }, theme)
    await page.waitForTimeout(250)
    await page.screenshot({ path: `/tmp/yuxi-reading-ui-${theme}.png` })
  }
  await page.getByRole('button', { name: '取 消', exact: true }).click()
  await page.setViewportSize({ width: 768, height: 960 })
  await page.waitForTimeout(400)
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '768px 出现横向溢出')
  await page.screenshot({ path: '/tmp/yuxi-reading-ui-768.png' })
  return { readingPosition: true, bottomFollow: true, manualScroll: true, personalNavigation: true, traceTabs: true, themes: ['light', 'dark'] }
}
