// 在独立浏览器打开开发前端后运行：playwright-cli run-code --filename=frontend/test/browser/cooperationAttention.js
// 使用确定性 HTTP fixture 验证真实 /agent 页面；所有 API 请求均被拦截，不修改服务端数据。
// prettier-ignore
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const agent = { agent_id: 'default-chatbot', name: '智能助手', can_run: true, configurable_items: {}, config_json: { context: {} } }
  let failed = false, second = false, finished = false, historyDelay = 700, extraCount = 0
  const submissions = []
  const pageErrors = []
  const onPageError = error => pageErrors.push(error.message)
  page.on('pageerror', onPageError)
  const summary = () => ({ sessions: [
    { session_id: 'attention-root', path: '/root', turn_status: 'completed' },
    { session_id: 'attention-child', path: '/root/child', name: '资料整理', turn_status: finished ? 'completed' : 'waiting', waiting_for: 'answer' },
    ...(second ? [{ session_id: 'attention-approval', path: '/root/review', name: '文件整理与发布前检查', turn_status: 'waiting', waiting_for: 'approval' }] : []),
    ...Array.from({ length: extraCount }, (_, index) => ({ session_id: `attention-extra-${index}`, path: `/root/extra-${index}`, name: `补充协作任务 ${index + 1}`, turn_status: 'waiting', waiting_for: 'answer' }))
  ] })
  const turn = (id) => ({
    id: `turn-${id}`, status: finished || id === 'attention-root' ? 'completed' : 'requires_action',
    yuxi: { current_run_id: `run-${id}`, output: [], runs: [], waitpoint: {
      id: `wait-${id}`, run_id: `run-${id}`, kind: id === 'attention-approval' ? 'approval' : 'answer',
      questions: [{ question_id: 'task-kind', question: '你希望优先处理哪类任务？', options: [{ label: '资料检索', value: '资料检索' }], allow_other: true }],
      calls: [{ call_id: 'call-publish', name: 'write_file', args: { path: '/report.md', content: '测试报告' }, allowed_decisions: ['approve', 'reject'] }]
    } }
  })
  const session = (id) => ({
    id, agent: { id: agent.agent_id }, status: turn(id).status,
    yuxi: { title: id === 'attention-root' ? '协作任务待办验证' : '资料整理', project_id: 'attention-project', current_turn: turn(id), tool_approval_mode: 'ask' }
  })
  await page.route('**/api/**', async route => {
    const request = route.request(), path = request.url().split('?')[0].replace(/^https?:\/\/[^/]+/, '')
    const reply = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/cooperation')) {
      if (failed) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture offline' }) })
      return reply(summary())
    }
    const match = path.match(/\/sessions\/(attention-[^/]+)/)
    if (match) {
      const id = match[1]
      if (path.endsWith('/events') && request.method() === 'POST') {
        submissions.push({ id, body: request.postDataJSON() })
        finished = true
        return reply({ turn_id: `turn-${id}`, run_id: `run-${id}`, status: 'completed' })
      }
      if (path.endsWith('/items')) {
        if (id === 'attention-child' && historyDelay) await page.waitForTimeout(historyDelay)
        return reply({ data: [], has_more: false, last_id: null, yuxi: { runs: [] } })
      }
      if (path.includes('/turns/')) return reply(turn(id))
      if (path.endsWith('/state')) return reply({ agent_state: {} })
      if (path.endsWith('/queue')) return reply({ inputs: [], status: 'idle' })
      if (path.endsWith('/attachments')) return reply({ attachments: [] })
      return reply(session(id))
    }
    if (path === '/api/v1/agents/sessions') return reply({ data: [session('attention-root')], has_more: false, last_id: null })
    if (path === '/api/agent') return reply({ agents: [agent] })
    if (path === '/api/agent/default-chatbot') return reply(agent)
    if (path === '/api/auth/me') return reply({ id: 1, uid: 'attention-browser-user', username: '协作验收用户', role: 'user' })
    if (path.endsWith('/system/info')) return reply({ success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } })
    if (path.endsWith('/system/health')) return reply({ status: 'ok' })
    if (path === '/api/projects') return reply([{ id: 'attention-project', name: '交互验证', workdir_path: '/test', threads: [] }])
    if (path.includes('/filesystem/tree')) return reply({ tree: [] })
    return reply({ data: [], databases: [], models: {}, first_run: false, enabled: false })
  })
  await page.addInitScript(() => {
    localStorage.setItem('user_token', 'attention-browser-fixture')
    localStorage.setItem('chat-ui-store', JSON.stringify({ sidebarCollapsed: false }))
  })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${origin}/agent/attention-root`)
  const pill = page.locator('.attention-pill')
  const card = page.locator('.attention-card')
  const list = card.locator('.attention-list')
  const screenshots = []
  /** 等待当前布局与有限动画完成后，读取最终 DOM 和截图。 */
  const settle = async () => {
    await page.mouse.move(1400, 20)
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    await page.waitForFunction(() => document.getAnimations().every(animation =>
      animation.effect.getTiming().iterations === Infinity || animation.playState !== 'running'))
  }
  /** 展开列表必须留在原卡片内，并复用原有胶囊按钮。 */
  const checkInline = async () => {
    check(await pill.getAttribute('aria-expanded') === 'true', '待办入口没有进入展开状态')
    check((await pill.textContent()).includes('收起待办'), '展开后没有提供收起动作')
    check(await page.locator('.ant-popover:visible').count() === 0, '协作待办仍通过浮层展示')
    check(await list.evaluate(el => el.parentElement.classList.contains('attention-list-collapse') && el.closest('.attention-card')?.contains(el)), '待办列表没有在原卡片内部展开')
    check(await page.evaluate(() => window.__attentionNodes.card === document.querySelector('.attention-card') && window.__attentionNodes.pill === document.querySelector('.attention-pill')), '展开时替换了原有卡片或胶囊 DOM')
    check(await card.evaluate(el => {
      const box = el.getBoundingClientRect()
      return box.left >= 0 && box.right <= innerWidth + 1 && el.scrollWidth <= el.clientWidth + 1
    }), '原地待办卡片横向溢出')
  }
  await pill.waitFor()
  check((await pill.textContent()).includes('资料整理 待回答'), '主会话没有发现子任务的回答请求')
  await pill.click()
  const dialog = page.locator('.panel-session-chat:visible [role="dialog"]')
  await dialog.waitFor()
  await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'dialog')
  check(await page.getByRole('tab', { name: '资料整理，待回答', exact: true }).count() === 1, '标签缺少待办徽标')
  await pill.click()
  check(await page.getByRole('tab', { name: '资料整理，待回答', exact: true }).count() === 1, '重复打开产生了重复标签')
  await page.getByRole('button', { name: '关闭 资料整理', exact: true }).click()
  check(await pill.isVisible(), '关闭子标签错误地清除了待办')
  await pill.click()
  await dialog.waitFor()
  await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'dialog')
  await page.getByRole('button', { name: '关闭 资料整理', exact: true }).click()
  const lateHistory = page.waitForResponse(response => response.url().includes('/sessions/attention-child/items'))
  await pill.click()
  await page.getByRole('tab', { name: '文件', exact: true }).click()
  await lateHistory
  await page.waitForFunction(async () => {
    const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
    return Boolean(useSessionRuntimeStore().getThreadState('attention-child')?.pendingInterrupt)
  })
  check(await page.evaluate(() => document.activeElement?.getAttribute('role') !== 'dialog'), '晚到的人工卡片抢走了其他标签的焦点')
  await pill.click()
  await dialog.waitFor()
  await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'dialog')
  historyDelay = 0
  await page.waitForFunction(() => [...document.querySelectorAll('.panel-session-chat [role="dialog"]')].some(el => el.offsetParent && getComputedStyle(el).opacity === '1'))
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-attention-inline-single.png', animations: 'disabled' })
  screenshots.push('/tmp/yuxi-cooperation-attention-inline-single.png')
  second = true
  await page.waitForFunction(() => document.querySelector('.attention-pill')?.textContent.includes('2 个协作任务'))
  await page.evaluate(() => { window.__attentionNodes = { card: document.querySelector('.attention-card'), pill: document.querySelector('.attention-pill') } })
  const collapsed = await card.evaluate(el => ({ height: el.getBoundingClientRect().height, radius: parseFloat(getComputedStyle(el).borderRadius) }))
  await pill.focus()
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => document.activeElement?.classList.contains('attention-item'))
  await checkInline()
  const expanded = await card.evaluate(el => ({ height: el.getBoundingClientRect().height, radius: parseFloat(getComputedStyle(el).borderRadius), transition: getComputedStyle(el).transitionProperty }))
  check(expanded.height > collapsed.height && expanded.radius < collapsed.radius, '胶囊没有原地展开为圆角卡片')
  check(expanded.transition.includes('border-radius'), '圆角变化缺少过渡')
  await page.keyboard.press('Tab')
  check(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')?.includes('待审批')), '键盘未进入下一条待办')
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => document.activeElement?.classList.contains('attention-pill') && document.activeElement.getAttribute('aria-expanded') === 'false')
  await list.waitFor({ state: 'hidden' })
  await pill.click()
  await list.waitFor()
  await pill.click()
  await list.waitFor({ state: 'hidden' })
  check((await pill.textContent()).includes('2 个协作任务'), '收起列表错误地消费了待办')
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: 1000 })
    const collapseSidebar = page.getByRole('button', { name: '折叠侧边栏', exact: true })
    if (width <= 1024 && await collapseSidebar.count()) await collapseSidebar.click()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(async value => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(value)
      }, theme)
      await pill.click()
      await page.waitForFunction(() => document.activeElement?.classList.contains('attention-item'))
      await settle()
      await checkInline()
      if (theme === 'dark') check(await card.evaluate(el => getComputedStyle(el).backgroundColor !== 'rgb(255, 255, 255)'), '暗色待办卡片仍使用浅色背景')
      const path = `/tmp/yuxi-cooperation-attention-inline-${theme}-${width}.png`
      await page.locator('.agent-view').screenshot({ path, animations: 'disabled' })
      screenshots.push(path)
      if (width === 1440) {
        const detailPath = `/tmp/yuxi-cooperation-attention-inline-card-${theme}.png`
        await card.screenshot({ path: detailPath, animations: 'disabled' })
        screenshots.push(detailPath)
      }
      await page.keyboard.press('Escape')
      await list.waitFor({ state: 'hidden' })
    }
  }
  await pill.click()
  await page.getByRole('button', { name: '文件整理与发布前检查 待审批，查看审批', exact: true }).click()
  await list.waitFor({ state: 'hidden' })
  check((await pill.textContent()).includes('2 个协作任务'), '选择待办错误地消费了请求')
  const approval = page.locator('.panel-session-chat:visible [role="dialog"]')
  await approval.getByRole('button', { name: '允许', exact: true }).waitFor()
  check(await page.getByRole('tab', { name: '文件整理与发布前检查，待审批', exact: true }).count() === 1, '审批没有打开正确标签')
  await page.setViewportSize({ width: 1440, height: 1000 })
  extraCount = 10
  await page.waitForFunction(() => document.querySelector('.attention-pill')?.textContent.includes('12 个协作任务'))
  await pill.click()
  await page.waitForFunction(() => document.activeElement?.classList.contains('attention-item'))
  await settle()
  await checkInline()
  check(await list.locator('.attention-item').count() === 12, '长列表丢失待办')
  check(await list.evaluate(el => el.clientHeight <= Math.min(280, innerHeight * 0.3) + 1 && el.scrollHeight > el.clientHeight && getComputedStyle(el).overflowY === 'auto'), '长列表没有受限高度和独立滚动')
  await list.hover()
  await page.mouse.wheel(0, 4000)
  await page.waitForFunction(() => {
    const el = document.querySelector('.attention-list')
    return el.scrollHeight - el.clientHeight - el.scrollTop < 2
  })
  check(await list.locator('.attention-item').last().isVisible(), '长列表末项无法滚动访问')
  await card.screenshot({ path: '/tmp/yuxi-cooperation-attention-inline-long-list.png', animations: 'disabled' })
  screenshots.push('/tmp/yuxi-cooperation-attention-inline-long-list.png')
  await page.keyboard.press('Escape')
  await list.waitFor({ state: 'hidden' })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const reduced = await page.evaluate(async () => {
    document.querySelector('.attention-pill').click()
    await Promise.resolve()
    const shell = document.querySelector('.attention-card')
    const collapse = shell.querySelector('.attention-list-collapse')
    return {
      entering: collapse.classList.contains('yuxi-collapse-enter-active'),
      durations: [shell, collapse, shell.querySelector('.attention-chevron')].map(node => getComputedStyle(node).transitionDuration)
    }
  })
  check(reduced.entering, '减少动态效果检查没有覆盖展开开始阶段')
  check(reduced.durations.every(duration => duration.split(',').every(value => parseFloat(value) === 0)), `减少动态效果偏好仍保留展开过渡：${JSON.stringify(reduced)}`)
  await page.waitForFunction(() => document.activeElement?.classList.contains('attention-item'))
  await page.keyboard.press('Escape')
  await list.waitFor({ state: 'hidden' })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  extraCount = 0
  await page.waitForFunction(() => document.querySelector('.attention-pill')?.textContent.includes('2 个协作任务'))
  await pill.click()
  await page.getByRole('button', { name: '资料整理 待回答，去回答', exact: true }).click()
  await dialog.waitFor()
  failed = true
  await page.getByText('协作状态更新失败，显示最近已知待办', { exact: true }).waitFor()
  check(await pill.isVisible(), '状态读取失败清除了最近已知待办')
  failed = false
  await page.locator('.attention-error').getByRole('button', { name: '重试', exact: true }).click()
  await page.locator('.attention-error').waitFor({ state: 'detached' })
  await pill.click()
  await list.waitFor()
  second = false
  await page.waitForFunction(() => document.querySelector('.attention-pill')?.textContent.includes('资料整理 待回答'))
  await list.waitFor({ state: 'hidden' })
  check(await pill.getAttribute('aria-expanded') === null, '多变单后仍保留列表展开意图')
  await dialog.locator('textarea').fill('资料检索')
  await dialog.getByRole('button', { name: '提交', exact: true }).click()
  await pill.waitFor({ state: 'detached' })
  check(submissions.length === 1 && submissions[0].id === 'attention-child', '回答提交给了其他会话')
  const event = submissions[0].body.events[0]
  check(event.turn_id === 'turn-attention-child' && event.waitpoint_id === 'wait-attention-child', '回答不属于目标等待点')
  check(await page.getByRole('tab', { name: '资料整理，待回答', exact: true }).count() === 0, '终态仍显示旧待办徽标')
  page.off('pageerror', onPageError)
  check(!pageErrors.length, `页面异常：${pageErrors.join('; ')}`)
  return { single: true, inlineMultiple: true, stableHeader: true, approval: true, slowRestoreFocus: true, hiddenDoesNotStealFocus: true, keyboard: true, retainedOnClose: true, retry: true, terminalRemoval: true, responseOwner: true, longList: 12, reducedMotion: true, widths: [1440, 1024, 768], screenshots, evidence: 'deterministic browser HTTP fixture' }
}
