// 独立 Vite 页面，使用固定协议 fixture 验证真实组件 DOM；不验证后端授权和会话执行。
// playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/sessionSidebar.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const origin = page.url().split('/').slice(0, 3).join('/')
  const agent = { agent_id: 'sidebar-agent', name: '智能助手', can_run: true, is_default: true, config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const project = { id: 'sidebar-project', name: '知识探索与长期研究项目', status: 'active', selection_status: 'selectable' }
  const sessions = Array.from({ length: 32 }, (_, index) => ({
    id: `sidebar-session-${index}`, object: 'agent.session', agent: { id: agent.agent_id, model: 'test:chat' }, metadata: {}, status: 'completed', created_at: 1792700000 - index, last_active_at: 1792700000 - index,
    yuxi: { title: index === 0 ? '调研浏览器智能体强化学习与长名称展示' : `研究对话 ${index + 1}`, project_id: index < 3 || index >= 26 ? project.id : null, is_pinned: false, unread: false, context: {}, config: {}, current_turn: null }
  }))
  let userId = 1
  let unavailableStatus = null
  let cachedUnavailableStatus = null
  let sessionReads = 0
  let sessionCreates = 0
  await page.route('https://api.github.com/repos/xerrors/Yuxi', route => route.fulfill({ json: { stargazers_count: 12345 } }))
  await page.route('**/api/**', async route => {
    const url = route.request().url()
    const path = url.split('?')[0].replace(origin, '')
    if (route.request().method() === 'POST' && /\/agents\/sessions(?:\/[^/]+\/events)?$/.test(path)) sessionCreates++
    let json = { data: [], files: [], skills: [], projects: [], providers: [], config: {} }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/system/health')) json = { status: 'ok' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path.endsWith('/auth/me')) json = { id: userId, uid: `sidebar-fixture-${userId}`, username: `侧栏验收${userId}`, role: 'superadmin' }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === '/api/agent/sidebar-agent') json = agent
    if (path === '/api/projects') json = [project]
    if (path === '/api/v1/agents/sessions') {
      sessionReads++
      json = { data: url.includes('is_pinned=true') ? [] : sessions, has_more: false }
    }
    const match = path.match(/^\/api\/v1\/agents\/sessions\/([^/]+)(?:\/(.*))?$/)
    if (match && match[1] === 'unavailable-thread' && unavailableStatus) return route.fulfill({ status: unavailableStatus, json: { detail: '恢复测试' } })
    const session = match && sessions.find(item => item.id === match[1])
    if (session && cachedUnavailableStatus && session.id === sessions[4].id && !match[2]) return route.fulfill({ status: cachedUnavailableStatus, json: { detail: '缓存线程恢复测试' } })
    if (session) {
      const resource = match[2] || ''
      if (resource === '' || resource === 'viewed') json = session
      if (resource === 'items') json = {
        data: [{ id: `message-${session.id}`, object: 'agent.session.item', type: 'message', role: 'user', status: 'completed', turn_id: `turn-${session.id}`,
          content: [{ type: 'input_text', text: `历史消息 ${session.id}` }], yuxi: { message_id: `message-${session.id}`, run_id: `run-${session.id}`, created_at: '2026-10-23T00:00:00Z' } }],
        has_more: false, last_id: `message-${session.id}`, yuxi: { runs: [] }
      }
      if (resource === 'state') json = { agent_state: {}, interrupt: null }
      if (resource === 'queue') json = { inputs: [], status: 'completed', queue_paused: false }
      if (resource === 'cooperation') json = { sessions: [], members: [], edges: [] }
      if (resource === 'feedback') json = { items: [] }
      if (resource === 'attachments') json = { attachments: [], files: [] }
      if (resource === 'events' && route.request().method() === 'GET') return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
    }
    return route.fulfill({ json })
  })
  await page.evaluate(() => {
    localStorage.setItem('user_token', 'sidebar-fixture')
    localStorage.setItem('chat-ui-store', JSON.stringify({ sidebarCollapsed: false }))
  })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(origin + '/agent')
  const panel = page.locator('#sidebar-session-navigation:visible, #sidebar-session-panel')
  /** 遮罩终色必须来自所在列表的实际背景，覆盖主侧栏与浮动/固定面板。 */
  const checkMaskSurface = async () => {
    const sample = await panel.locator('.session-item:not(.active):not(:hover) .actions-mask').first().evaluate(mask => {
      let surface = mask.parentElement
      while (surface && getComputedStyle(surface).backgroundColor === 'rgba(0, 0, 0, 0)') surface = surface.parentElement
      return {
        gradient: getComputedStyle(mask).backgroundImage,
        background: surface ? getComputedStyle(surface).backgroundColor : ''
      }
    })
    check(sample.background && sample.gradient.includes(sample.background), `会话遮罩与所在表面不一致：${JSON.stringify(sample)}`)
  }
  const workspace = page.locator('.agent-view > .agent-session-workspace')
  const trigger = page.getByRole('button', { name: '显示对话列表', exact: true })
  const showPanel = async () => {
    await trigger.focus()
    await page.keyboard.press('Enter')
    await panel.waitFor()
  }
  const hidePanel = async () => {
    const unpin = page.getByRole('button', { name: '收起对话侧栏', exact: true })
    if (await unpin.count()) { await unpin.focus(); await page.keyboard.press('Enter') }
    else await page.keyboard.press('Escape')
    await panel.waitFor({ state: 'hidden' })
  }
  const pin = page.getByRole('button', { name: '展开对话侧栏', exact: true })
  const main = page.locator('#app-router-view')
  await panel.getByText(project.name, { exact: true }).waitFor()
  await panel.locator('.project-toggle').click()
  await panel.getByText(sessions[0].yuxi.title, { exact: true }).waitFor()
  await checkMaskSurface()
  await page.evaluate(() => { window.__sidebarOriginal = document.querySelector('.sidebar-sessions') })
  const brandStyle = await page.locator('.sidebar-brand .brand-name').evaluate(el => {
    const style = getComputedStyle(el)
    return { name: el.textContent, color: style.color, size: style.fontSize, weight: style.fontWeight, lineHeight: style.lineHeight }
  })
  const readHeaderSpacing = el => {
    const style = getComputedStyle(el)
    return [style.height, style.paddingLeft, style.paddingRight, style.marginTop]
  }
  const headerSpacing = await page.locator('.sidebar-brand').evaluate(readHeaderSpacing)
  headerSpacing[1] = '10px'
  headerSpacing[2] = '10px'
  check(await page.locator('.nav').getByRole('link', { name: '对话', exact: true }).getAttribute('href') === '/agent', '未选择线程时对话入口应进入空白编辑页')
  const newChatIcon = page.locator('.primary-new-chat')
  check(await page.locator('.header .session-panel-new-chat').count() === 0, 'AppLayout不能重复展示整行新建对话')
  check(await newChatIcon.innerText() === '', '对话右侧仅展示新建图标')
  check(await newChatIcon.evaluate(el => el.parentElement === document.querySelector('.primary-nav-row') && !el.closest('.nav-item')), '新建图标与返回对话入口必须是独立链接')
  await page.locator('.primary-nav-row > .nav-item').click()
  await page.mouse.move(1200, 400)
  check(await newChatIcon.evaluate(el => getComputedStyle(el).opacity === '0'), '鼠标点击后移出整行也应隐藏，不能因点击焦点残留而常驻')
  for (const theme of ['light', 'dark']) {
    await page.evaluate(async value => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(value) }, theme)
    await page.mouse.move(1200, 400)
    await page.waitForTimeout(180)
    check(await newChatIcon.evaluate(el => getComputedStyle(el).opacity === '0' && getComputedStyle(el).pointerEvents === 'none'), '鼠标移出对话行后新建图标默认隐藏且不拦截点击')
    const initialStyle = await newChatIcon.evaluate(el => ({ background: getComputedStyle(el).backgroundColor, color: getComputedStyle(el).color }))
    await page.locator('.primary-nav-row > .nav-item').hover()
    check(await newChatIcon.evaluate(el => getComputedStyle(el).opacity === '1' && getComputedStyle(el).pointerEvents === 'auto'), '悬停对话行任意位置应显示新建图标')
    await newChatIcon.hover()
    await page.waitForTimeout(180)
    const hoverStyle = await newChatIcon.evaluate(el => ({ background: getComputedStyle(el).backgroundColor, color: getComputedStyle(el).color }))
    check(initialStyle.background === 'rgba(0, 0, 0, 0)' && hoverStyle.background === initialStyle.background, '新建图标默认和悬停均不得有背景')
    check(initialStyle.color !== hoverStyle.color, '悬停新建图标应强化颜色')
  }
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('light') })
  await page.mouse.move(1200, 400)
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).focus()
  await page.keyboard.press('Tab')
  check(await newChatIcon.evaluate(el => getComputedStyle(el).opacity === '1'), '键盘聚焦对话行时应显示新建入口')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Tab')
  check(await newChatIcon.evaluate(el => el === document.activeElement && getComputedStyle(el).opacity === '1'), 'Tab应能聚焦并显示新建入口')
  const before = await main.boundingBox()
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  await panel.waitFor({ state: 'hidden' })
  check(Math.abs((await page.locator('.foo').boundingBox()).y + (await page.locator('.foo').boundingBox()).height - 1000) < 2, '收起后用户菜单仍须位于底部')
  check(await newChatIcon.count() === 0, '收起后的图标栏不额外挤入新建图标')
  check(await trigger.evaluate(el => el.tagName === 'DIV' && el.childElementCount === 0), '入口应是无可见按钮的触发区域')
  await trigger.waitFor()
  const triggerBox = await trigger.boundingBox()
  await page.mouse.move(triggerBox.x + 10, triggerBox.y + 10)
  await panel.waitFor()
  check(await main.evaluate(el => el.getBoundingClientRect().x) < before.x, '浮层不应为正文保留旧侧栏空间')
  const panelBrand = await panel.locator('.brand-name').evaluate(el => {
    const style = getComputedStyle(el)
    return { name: el.textContent, color: style.color, size: style.fontSize, weight: style.fontWeight, lineHeight: style.lineHeight }
  })
  check(JSON.stringify(panelBrand) === JSON.stringify(brandStyle), '面板品牌名称、颜色和字体必须与原侧栏一致')
  check(JSON.stringify(await panel.locator('.session-panel-header').evaluate(readHeaderSpacing)) === JSON.stringify(headerSpacing), '面板头部左右padding应为10px，高度和上边距与原侧栏一致')
  check(await panel.locator('.brand-logo').count() === 0, '对话面板不应重复展示Logo')
  check(await panel.locator('.brand-name').evaluate(el => getComputedStyle(el).marginLeft) === '0px', '无Logo的标题不应保留图标间距')
  check(await panel.locator('.session-panel-actions .lucide-pin').count() === 0, '侧栏切换操作应使用侧栏图标')
  check(await panel.getByRole('link', { name: '新建对话', exact: true }).isVisible(), '面板必须展示整行新建对话入口')
  check(await page.locator('.nav').getByRole('button', { name: '搜索', exact: true }).count() === 0, '收起后的图标栏不应保留搜索按钮')
  check(await panel.evaluate(el => getComputedStyle(el).boxShadow) === 'none', '悬浮面板不应有阴影')
  await page.waitForTimeout(220)
  const floatingBox = await panel.boundingBox()
  const railBox = await page.locator('.header').boundingBox()
  check(floatingBox.x - railBox.x - railBox.width <= 4 && floatingBox.y === 4, '浮层应缩小与图标栏及顶部的间距')
  await panel.hover()
  await page.waitForTimeout(250)
  check(await panel.isVisible(), '从入口移入卡片时不应关闭')
  await page.mouse.move(1200, 400)
  await panel.waitFor({ state: 'hidden' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  await panel.waitFor()
  check(await panel.getByRole('button', { name: '搜索', exact: true }).evaluate(el => el === document.activeElement), '键盘打开后焦点应直接进入面板搜索')
  await page.keyboard.press('Tab')
  check(await page.evaluate(() => !!document.activeElement.closest('#sidebar-session-panel')), 'Tab 应继续浏览列表内容')
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  check(await trigger.evaluate(el => el === document.activeElement), 'Escape 应将焦点交还入口')
  await showPanel()
  await pin.click()
  await page.mouse.move(1200, 400)
  await page.waitForTimeout(250)
  check(await panel.isVisible(), '固定列不应在指针移出后消失')
  await page.waitForFunction(() => Math.abs(document.querySelector('.agent-view > .agent-session-workspace').getBoundingClientRect().x - 302) < 1)
  const fixed = await panel.boundingBox()
  const seam = await page.locator('.header').evaluate(el => ({
    width: parseFloat(getComputedStyle(el).borderRightWidth),
    style: getComputedStyle(el).borderRightStyle,
    right: el.getBoundingClientRect().right
  }))
  check(seam.width > 0 && seam.style === 'solid' && Math.abs(seam.right - fixed.x) < 2, '固定列表左侧必须由紧邻导航栏提供可见分界')
  check(Math.abs((await workspace.boundingBox()).x - fixed.x - fixed.width) < 2, '固定列必须给正文让位')
  check(await page.locator('.header .nav-text').first().evaluate(el => getComputedStyle(el).opacity) === '0', '固定列表不能展开主导航')
  const unpin = page.getByRole('button', { name: '收起对话侧栏', exact: true })
  await main.click({ position: { x: 900, y: 50 } })
  await unpin.focus()
  await page.keyboard.press('Enter')
  await panel.waitFor({ state: 'hidden' })
  check(await trigger.evaluate(el => el === document.activeElement), '取消固定应直接收起并归还入口焦点')
  await page.waitForFunction(() => Math.abs(document.querySelector('#app-router-view').getBoundingClientRect().x - 52) < 1)
  await showPanel()
  await main.click({ position: { x: 700, y: 50 } })
  await panel.waitFor({ state: 'hidden' })
  await showPanel()
  await page.evaluate(() => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/agent?project_id=sidebar-project'))
  await panel.getByRole('link', { name: '新建对话', exact: true }).click()
  await page.waitForURL(origin + '/agent')
  check(sessionCreates === 0, '新建对话入口只进入编辑页，不应自动提交会话')
  await panel.getByRole('button', { name: '搜索', exact: true }).click()
  const searchDialog = page.getByRole('dialog', { name: '搜索对话', exact: true })
  await searchDialog.waitFor()
  await searchDialog.getByRole('textbox').fill('研究')
  await page.waitForTimeout(250)
  check(await panel.isVisible(), '使用面板搜索时所属列表应保持可见')
  await page.keyboard.press('Escape')
  await searchDialog.waitFor({ state: 'hidden' })
  check(await panel.isVisible(), 'Escape 应优先关闭搜索框')
  await panel.locator('.session-item').first().hover()
  await panel.getByRole('button', { name: '对话操作', exact: true }).first().click()
  const rename = page.getByRole('menuitem', { name: '重命名', exact: true })
  await rename.hover()
  await rename.focus()
  await page.mouse.move(1200, 400)
  await page.waitForTimeout(250)
  check(await panel.isVisible(), '移入 portal 操作菜单不能丢失卡片')
  await rename.click()
  await page.getByRole('dialog').waitFor()
  await page.waitForTimeout(250)
  check(await panel.isVisible(), '重命名弹窗打开时应保留所属列表')
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  check(await panel.isVisible(), 'Escape 应先关闭弹窗并保留所属列表')
  await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  await panel.getByText(sessions[0].yuxi.title, { exact: true }).waitFor()
  check(await panel.locator('.project-toggle').getAttribute('aria-expanded') === 'true', '展开导航后必须保留项目展开状态')
  check(await page.evaluate(() => window.__sidebarOriginal === document.querySelector('.sidebar-sessions')), '普通导航列表应持续挂载，面板共享其展开状态')
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  await panel.waitFor({ state: 'hidden' })
  const screenshots = []
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: 1000 })
    for (const theme of ['light', 'dark']) {
      await page.evaluate(async value => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(value) }, theme)
      await showPanel()
      await panel.waitFor()
      await page.waitForTimeout(220)
      await checkMaskSurface()
      const box = await panel.boundingBox()
      check(box.x >= 52 && box.x + box.width <= width && box.y >= 0 && box.y + box.height <= 1000, '卡片超出支持视口')
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '页面横向溢出')
      check(await panel.locator('.session-list').evaluate(el => el.scrollHeight > el.clientHeight), '长列表应支持独立滚动')
      await panel.click({ position: { x: 2, y: 24 } })
      if (width === 1440 || (width === 768 && theme === 'dark')) {
        const path = `/tmp/yuxi-session-sidebar-floating-${theme}-${width}.png`
        await page.screenshot({ path })
        screenshots.push(path)
      }
      await pin.click()
      await main.click({ position: { x: width - 340, y: 50 } })
      check(await panel.isVisible(), '点击正文不能关闭固定列表')
      await page.waitForFunction(() => Math.abs(document.querySelector('.agent-view > .agent-session-workspace').getBoundingClientRect().x - 302) < 1)
      check(Math.abs((await workspace.boundingBox()).x - 302) < 2, '不同宽度的固定布局错误')
      await checkMaskSurface()
      if (width === 1440 && theme === 'light') {
        const path = '/tmp/yuxi-session-sidebar-pinned.png'
        await page.screenshot({ path })
        screenshots.push(path)
      }
      await hidePanel()
      await panel.waitFor({ state: 'hidden' })
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('light') })
  const navigate = path => page.evaluate(path => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(path), path)
  await navigate(`/agent/${sessions[28].id}`)
  await showPanel()
  const currentProjectItem = panel.locator('[aria-current="page"]')
  await currentProjectItem.waitFor()
  check((await currentProjectItem.innerText()).includes(sessions[28].yuxi.title), '项目第六条必须自动展开并选中')
  check(await page.locator('#sidebar-session-panel').count() === 1, '切换线程只能保留一个独立面板实例')
  await panel.locator('.project-toggle').click()
  await page.waitForFunction(() => document.querySelector('#sidebar-session-panel .project-toggle').getAttribute('aria-expanded') === 'false')
  const readsBeforeRefresh = sessionReads
  await page.evaluate(async () => {
    const { useChatThreadsStore } = await import('/src/modules/session/model/chatThreads.js')
    await useChatThreadsStore().loadThreads()
  })
  check(sessionReads > readsBeforeRefresh, '折叠负例必须实际刷新会话列表')
  check(await panel.locator('.project-toggle').getAttribute('aria-expanded') === 'false', '手动折叠后刷新不能强制展开')
  await hidePanel()
  await page.evaluate(() => import('/src/pages/DashboardView.vue'))
  await navigate('/agent')
  await showPanel()
  await pin.click()
  await page.waitForFunction(() => Math.abs(document.querySelector('.agent-view > .agent-session-workspace').getBoundingClientRect().x - 302) < 1)
  const fixedDashboardFrames = await page.evaluate(async () => {
    const frames = []
    const started = performance.now()
    const sampling = new Promise(resolve => {
      function sample() {
        const dashboard = document.querySelector('.dashboard-container')
        if (dashboard) frames.push({ x: dashboard.getBoundingClientRect().x, mainX: document.querySelector('#app-router-view').getBoundingClientRect().x, panelCount: [...document.querySelectorAll('#sidebar-session-panel')].filter(node => node.getClientRects().length).length })
        if (performance.now() - started < 450) requestAnimationFrame(sample)
        else resolve()
      }
      requestAnimationFrame(sample)
    })
    await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/dashboard')
    await sampling
    return frames
  })
  check(fixedDashboardFrames.length > 5, '必须逐帧读取真实Dashboard页面')
  check(fixedDashboardFrames.every(frame => frame.x === 52 && frame.mainX === 52 && frame.panelCount === 0), 'Dashboard首帧起必须无对话面板且横坐标稳定，不得继承对话占位动画')
  await page.screenshot({ path: '/tmp/yuxi-sidebar-dashboard-transition-fixed.png' })
  await navigate('/agent')
  await panel.waitFor()
  check(await page.getByRole('button', { name: '收起对话侧栏', exact: true }).isVisible(), '返回对话应恢复常驻展开')
  await hidePanel()
  for (const path of ['/agent-manage', '/workspace', '/extensions']) {
    await showPanel()
    await pin.click()
    await navigate(path)
    await page.waitForURL(origin + path)
    check(await panel.isHidden(), '非对话页面必须隐藏独立对话面板：' + path)
    check(await trigger.count() === 0, '非对话页不应提供独立面板触发区域：' + path)
    check(await page.locator('.app-layout.sessions-pinned').count() === 0, 'AppLayout不得拥有对话面板占位样式')
    await page.waitForFunction(() => Math.abs(document.querySelector('#app-router-view').getBoundingClientRect().x - 52) < 1)
    await page.mouse.move(80, 20)
    await page.waitForTimeout(250)
    check(await panel.isHidden(), '非对话页移过原触发区不能重新打开面板')
    if (path === '/agent-manage') {
      const imagePath = '/tmp/yuxi-session-sidebar-agent-manage-collapsed.png'
      await page.screenshot({ path: imagePath })
      screenshots.push(imagePath)
    }
    await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
    await panel.waitFor()
    await panel.getByText(project.name, { exact: true }).waitFor()
    await page.waitForFunction(() => Math.abs(document.querySelector('.header').getBoundingClientRect().width - 230) < 1)
    check(await panel.locator('.session-panel-header').count() === 0, '原导航中的列表不应显示独立面板头部')
    check(await page.locator('.sidebar-brand').getByRole('button', { name: '搜索', exact: true }).count() === 1, '展开原导航仍应保留搜索')
    check(Math.abs((await page.locator('.foo').boundingBox()).y + (await page.locator('.foo').boundingBox()).height - 1000) < 2, '非对话页用户菜单应保持在底部')
    if (path === '/agent-manage') {
      const imagePath = '/tmp/yuxi-session-sidebar-agent-manage-expanded.png'
      await page.screenshot({ path: imagePath })
      screenshots.push(imagePath)
    }
    await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
    await panel.waitFor({ state: 'hidden' })
    await navigate('/agent')
    await trigger.waitFor()
    check(await panel.isHidden(), '返回AgentView不应自动恢复已关闭的独立面板')
  }
  const returnSession = sessions[3]
  await navigate(`/agent/${returnSession.id}`)
  const historyMessage = page.locator('.agent-view .history-display-item').getByText(`历史消息 ${returnSession.id}`, { exact: true })
  await historyMessage.waitFor()
  const editor = page.locator('.agent-view [contenteditable="true"]').first()
  await editor.fill('返回线程时保留这段未发送草稿')
  for (const destination of ['智能体', '个人空间']) {
    await page.locator('.nav').getByRole('link', { name: destination, exact: true }).click()
    await page.waitForURL(destination === '智能体' ? origin + '/agent-manage' : origin + '/workspace')
    const conversationEntry = page.locator('.nav').getByRole('link', { name: '对话', exact: true })
    check(await conversationEntry.getAttribute('href') === `/agent/${returnSession.id}`, '离开后对话入口应指向之前的线程')
    await conversationEntry.click()
    await page.waitForURL(origin + `/agent/${returnSession.id}`)
    await historyMessage.waitFor()
    check(await editor.innerText() === '返回线程时保留这段未发送草稿', '返回对话应保留原线程的未发送草稿')
    check(await conversationEntry.evaluate(el => el.classList.contains('active')), '已有线程页应高亮全局对话入口')
  }
  await showPanel()
  await pin.click()
  await page.reload()
  await historyMessage.waitFor()
  await panel.waitFor()
  check(await page.getByRole('button', { name: '收起对话侧栏', exact: true }).isVisible(), '刷新对话页应恢复常驻展开')
  await navigate('/dashboard')
  await page.reload()
  await page.locator('.dashboard-container').waitFor()
  check(await page.locator('#sidebar-session-panel').isHidden() && (await main.boundingBox()).x === 52, '刷新Dashboard不能显示对话面板或为其占位')
  const rememberedEntry = page.locator('.nav').getByRole('link', { name: '对话', exact: true })
  check(await rememberedEntry.getAttribute('href') === `/agent/${returnSession.id}`, '刷新其他页后仍须记住原线程')
  await rememberedEntry.click()
  await historyMessage.waitFor()
  await panel.waitFor()
  check(await page.getByRole('button', { name: '收起对话侧栏', exact: true }).isVisible(), '刷新其他页再返回对话仍须常驻展开')
  await page.mouse.move(1200, 400)
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/yuxi-sidebar-refresh-restored.png' })
  await hidePanel()
  await page.reload()
  await historyMessage.waitFor()
  check(await panel.isHidden(), '显式收起偏好须跨刷新保持')
  userId = 2
  await navigate('/dashboard')
  await page.reload()
  await page.locator('.dashboard-container').waitFor()
  check(await rememberedEntry.getAttribute('href') === '/agent', '另一账号不能继承原账号的线程目标')
  await rememberedEntry.click()
  await trigger.waitFor()
  check(await panel.isHidden(), '另一账号不能继承面板展开偏好')
  userId = 1
  await navigate('/dashboard')
  await page.reload()
  await page.locator('.dashboard-container').waitFor()
  check(await rememberedEntry.getAttribute('href') === `/agent/${returnSession.id}`, '返回原账号仍保留其目标')
  await rememberedEntry.click()
  await historyMessage.waitFor()
  const returnImage = '/tmp/yuxi-session-return-thread.png'
  await page.screenshot({ path: returnImage })
  screenshots.push(returnImage)
  await page.locator('.nav').getByRole('link', { name: '对话', exact: true }).click()
  await page.waitForURL(origin + `/agent/${returnSession.id}`)
  check(await historyMessage.isVisible(), '已在对话页时点击全局入口不能新建对话')
  await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  await page.waitForFunction(() => Math.abs(document.querySelector('.header').getBoundingClientRect().width - 230) < 1)
  await page.mouse.move(1200, 400)
  await page.screenshot({ path: '/tmp/yuxi-sidebar-new-chat-icon-hidden.png' })
  await page.locator('.primary-nav-row > .nav-item').hover()
  await page.screenshot({ path: '/tmp/yuxi-sidebar-new-chat-icon.png' })
  await newChatIcon.focus()
  await page.keyboard.press('Enter')
  await page.waitForURL(origin + '/agent')
  await historyMessage.waitFor({ state: 'hidden' })
  check(await editor.innerText() === '', '显式新建应进入独立的空白编辑页')
  await page.locator('.nav').getByRole('link', { name: '智能体', exact: true }).click()
  const draftEntry = page.locator('.nav').getByRole('link', { name: '对话', exact: true })
  check(await draftEntry.getAttribute('href') === '/agent', '显式新建后全局入口不应回到旧线程')
  await draftEntry.click()
  await page.waitForURL(origin + '/agent')
  check(sessionCreates === 0, '切换入口和显式新建都不能自动提交会话')
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  await page.reload()
  await trigger.waitFor()
  check(await draftEntry.getAttribute('href') === '/agent', '显式新建清理目标须跨刷新保持')
  for (const status of [503, 404]) {
    unavailableStatus = status
    const unavailableResponse = page.waitForResponse(response => response.url().endsWith('/unavailable-thread') && response.status() === status)
    await navigate('/agent/unavailable-thread')
    await unavailableResponse
    if (status === 503) {
      check(page.url().endsWith('/agent/unavailable-thread'), '临时服务错误不能丢掉目标路由')
      await navigate('/dashboard')
      await page.reload()
      await page.locator('.dashboard-container').waitFor()
      check(await draftEntry.getAttribute('href') === '/agent/unavailable-thread', '临时错误不能清掉持久导航目标')
    } else {
      await page.waitForURL(origin + '/agent')
      await navigate('/dashboard')
      await page.reload()
      await page.locator('.dashboard-container').waitFor()
      check(await draftEntry.getAttribute('href') === '/agent', '确定失效应清掉持久目标')
    }
  }
  for (const status of [503, 404]) {
    cachedUnavailableStatus = status
    const target = `/agent/${sessions[4].id}`
    const response = page.waitForResponse(response => response.url().endsWith(`/${sessions[4].id}`) && response.status() === status)
    await navigate(target)
    await response
    if (status === 404) await page.waitForURL(origin + '/agent')
    else check(page.url() === origin + target, '缓存条目的临时错误也必须保留目标路由')
    await navigate('/dashboard')
    await page.reload()
    await page.locator('.dashboard-container').waitFor()
    check(await draftEntry.getAttribute('href') === (status === 404 ? '/agent' : target), '缓存条目确定失效清目标、临时失败保留目标')
  }
  cachedUnavailableStatus = null
  await navigate('/agent')
  sessions[3].status = 'failed'
  await showPanel()
  await pin.click()
  await panel.locator('.recent-list [aria-label="执行失败"]').waitFor({ timeout: 14000 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  for (const target of [page.locator('.header'), panel, workspace]) {
    check(await target.evaluate(el => getComputedStyle(el).transitionDuration) === '0s', '减少动态效果时应关闭侧栏与正文过渡')
  }
  check(sessionReads >= 2, '协议 fixture 未经过真实布局加载')
  return { hover: 'passed', keyboard: 'passed', pin: 'passed', unpinCloses: 'passed', panelSearch: 'passed', newChat: 'passed', agentViewScope: 'passed', singlePanel: 'passed', currentProjectReveal: 'passed', collapsedRefresh: 'passed', originalSidebarHistory: 'passed', returnToThread: 'passed', draftPreserved: 'passed', branding: 'passed', portalMenu: 'passed', sharedListState: 'passed', refreshRecovery: 'passed', accountScope: 'passed', dashboardStableFrames: fixedDashboardFrames.length, visibleStatusSync: 'passed', widths: [1440, 1024, 768], themes: ['light', 'dark'], screenshots }
}
