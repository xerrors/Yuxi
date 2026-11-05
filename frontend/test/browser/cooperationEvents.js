// 在独立已打开前端运行：playwright-cli -s=session-cooperation run-code --filename=frontend/test/browser/cooperationEvents.js
// 使用确定性 HTTP fixture 验证真实页面组件与路由，不修改服务端数据。
// prettier-ignore
async (page) => {
  await page.unrouteAll({ behavior: 'wait' })
  const origin = page.url().split('/').slice(0, 3).join('/')
  const check = (value, message) => { if (!value) throw new Error(message) }
  const errors = []
  const onError = error => errors.push(error.message)
  page.on('pageerror', onError)
  let edgeCase = false
  const root = 'event-root', other = 'event-other', child = 'event-quicksort', grandchild = 'event-factcheck'
  const ids = [root, child, 'event-fx', 'event-return', grandchild, other]
  const names = { [root]: '并行研究', [other]: '研究总览', [child]: 'quicksort', 'event-fx': 'fx-rate', 'event-return': 'return-flow', [grandchild]: 'factcheck' }
  const agent = { agent_id: 'default-chatbot', name: '智能助手', can_run: true, configurable_items: {}, config_json: { context: {} } }
  const parent = id => id === root || id === other ? null : id === grandchild ? child : root
  const treeRoot = id => id === other ? other : root
  const identity = id => ({ session_id: id, tree_root_session_id: treeRoot(id), parent_session_id: parent(id), name: names[id], title: names[id], path: parent(id) ? `/root/${names[id]}` : '/root' })
  const turn = id => ({ id: `turn-${id}`, status: 'completed', yuxi: { current_run_id: `run-${id}`, result_run_id: `run-${id}`, runs: [], output: [], waitpoint: null } })
  const session = id => ({ id, agent: { id: agent.agent_id, model: 'test:mock' }, status: 'completed', created_at: 1793260800,
    yuxi: { title: names[id], project_id: 'event-project', current_turn: turn(id), parent_session_id: parent(id), tool_approval_mode: 'always_trust' } })
  const summary = id => ({ tree_root_session_id: treeRoot(id), sessions: ids.filter(member => treeRoot(member) === treeRoot(id)).map(member => ({
    ...identity(member), turn_id: `turn-${member}`, current_run_id: `run-${member}`, result_run_id: `run-${member}`, turn_status: 'completed', run_status: 'completed'
  })) })
  const originFor = id => id === grandchild ? child : id === child || id === other ? root : null
  const items = id => {
    let outputIndex = 0
    const meta = messageId => ({ run_id: `run-${id}`, message_id: messageId, output_index: outputIndex++, created_at: '2026-10-29T04:00:00Z' })
    const message = (key, messageId, text, phase = 'commentary') => ({ id: key, type: 'message', role: 'assistant', status: 'completed', turn_id: `turn-${id}`, phase, content: [{ type: 'output_text', text }], yuxi: meta(messageId) })
    const user = { id: `input-${id}`, type: 'message', role: 'user', status: 'completed', turn_id: `turn-${id}`, content: [{ type: 'input_text', text: id === root ? '请并行完成排序分析、汇率研究和收益核验。' : `请完成 ${names[id]} 的核验任务。` }], yuxi: { ...meta(1), input_id: `input-${id}` } }
    if (id !== root) return [user, message(`final-${id}`, 2, '核验已完成，结果和相关资料已经整理。', 'final_answer')]
    const result = [user, message('intro', 2, '三路任务相互独立，我将分别派发工作。')]
    if (edgeCase) {
      result.push({ id: 'call-shell', type: 'function_call', call_id: 'shell', name: 'bash', arguments: JSON.stringify({ command: 'cat report.json' }), status: 'completed', turn_id: `turn-${id}`, yuxi: meta(2) })
      result.push({ id: 'output-shell', type: 'function_call_output', call_id: 'shell', output: JSON.stringify({ sessions: [{ ...identity(child), title: '普通文件中的同名字段' }] }), status: 'completed', turn_id: `turn-${id}`, yuxi: { ...meta(2), call_item_id: 'call-shell' } })
    }
    const calls = (name, messageId, targets) => {
      for (const [index, target] of targets.entries()) {
        const key = `${name}-${index}`
        const args = name === 'create_session' ? { name: names[target] } : name === 'wait_inputs' ? { input_ids: [`task-${target}`] } : name === 'get_result' ? { input_id: `task-${target}` } : {}
        const payload = name === 'list_sessions' ? { scope: 'project', sessions: ids.map(identity) } : { ...identity(target), input_id: `task-${target}`, results: [{ session_id: target, input_id: `task-${target}`, terminal: true }] }
        result.push({ id: `call-${key}`, type: 'function_call', call_id: key, name, arguments: JSON.stringify(args), status: 'completed', turn_id: `turn-${id}`, yuxi: meta(messageId) })
        result.push({ id: `output-${key}`, type: 'function_call_output', call_id: key, output: JSON.stringify(payload), status: 'completed', turn_id: `turn-${id}`, yuxi: { ...meta(messageId + 1), call_item_id: `call-${key}` } })
      }
    }
    calls('create_session', 3, [child, 'event-fx', 'event-return'])
    result.push(message('dispatched', 5, '三路工作已派发，现在等待它们交付。'))
    calls('wait_inputs', 6, [child, 'event-fx', 'event-return'])
    calls('list_sessions', 8, [root])
    calls('get_result', 8, [grandchild, other])
    if (edgeCase) {
      result.push({ id: 'call-error', type: 'function_call', call_id: 'error', name: 'send_message', arguments: JSON.stringify({ target: child }), status: 'failed', turn_id: `turn-${id}`, yuxi: meta(9) })
      result.push({ id: 'output-error', type: 'function_call_output', call_id: 'error', output: '会话已归档，请选择其他目标。', error: '协作消息未投递', status: 'failed', turn_id: `turn-${id}`, yuxi: { ...meta(9), call_item_id: 'call-error' } })
    }
    result.push(message('final', 10, '三个任务均已完成。排序分析、汇率资料和收益核验可以分别打开查看。', 'final_answer'))
    return result
  }
  await page.route('**/api/**', async route => {
    const request = route.request(), path = request.url().split('?')[0].replace(/^https?:\/\/[^/]+/, '')
    const reply = json => route.fulfill({ json })
    const web = path.match(/\/agent\/threads\/(event-[^/]+)\/(input-origins|navigation|feedback)/)
    if (web) {
      const [, id, kind] = web
      if (kind === 'navigation') return reply(identity(id))
      if (kind === 'input-origins') {
        const source = originFor(id)
        return reply({ origins: { [`input-${id}`]: source ? { kind: 'session', label: names[source], target: identity(source) } : { kind: 'schedule', label: '定时任务', target: null } } })
      }
      return reply({ items: [], has_more: false })
    }
    const match = path.match(/\/sessions\/(event-[^/]+)/)
    if (match) {
      const id = match[1]
      if (path.endsWith('/cooperation')) return reply(summary(id))
      if (path.endsWith('/items')) return reply({ data: items(id), has_more: false, yuxi: { runs: [{ id: `run-${id}`, turn_id: `turn-${id}`, status: 'completed', execution_seq: 1, started_at: '2026-10-29T04:00:00Z', finished_at: '2026-10-29T04:00:03Z' }] } })
      if (path.includes('/turns/')) return reply(turn(id))
      if (path.endsWith('/state')) return reply({ agent_state: {} })
      if (path.endsWith('/queue')) return reply({ inputs: [], status: 'ready' })
      return reply(session(id))
    }
    if (path === '/api/v1/agents/sessions') return reply({ data: [session(root), session(other)], has_more: false })
    if (path === '/api/agent') return reply({ agents: [agent] })
    if (path === '/api/agent/default-chatbot') return reply(agent)
    if (path === '/api/auth/me' || path.endsWith('/users/me')) return reply({ id: 1, uid: 'browser_fixture', username: '测试用户', role: 'user' })
    if (path === '/api/projects') return reply([{ id: 'event-project', name: '研究项目', workdir_path: 'projects/event', threads: [] }])
    if (path.includes('/filesystem/tree')) return reply({ tree: [] })
    return reply({ data: [], databases: [], models: {}, first_run: false, enabled: false })
  })
  await page.addInitScript(() => localStorage.setItem('user_token', 'cooperation-events-browser-fixture'))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${origin}/agent/${root}`)
  await page.getByText('三个任务均已完成。排序分析、汇率资料和收益核验可以分别打开查看。', { exact: true }).waitFor()
  const expand = async () => {
    for (const button of await page.locator('.process-summary:visible').all()) if (await button.getAttribute('aria-expanded') === 'false') await button.click()
    await page.locator('.tool-calls-summary').first().waitFor()
    for (const button of await page.locator('.tool-calls-summary:visible').all()) if (await button.getAttribute('aria-expanded') === 'false') await button.click()
  }
  await expand()
  const create = page.locator('.cooperation-event:visible').filter({ has: page.locator('.event-action', { hasText: /^创建$/ }) })
  await create.waitFor()
  check(await create.count() === 1 && await create.locator('button.event-target').count() === 3, '三个连续创建没有合并或丢失独立入口')
  const wait = page.locator('.cooperation-event:visible').filter({ has: page.locator('.event-action', { hasText: /^等待$/ }) })
  await wait.waitFor()
  check(await wait.count() === 1 && await wait.locator('button.event-target').count() === 3, '三个等待没有合并')
  for (const group of [create, wait]) {
    for (const [id, name] of [[child, 'quicksort'], ['event-fx', 'fx-rate'], ['event-return', 'return-flow']]) {
      await group.getByRole('button', { name, exact: true }).click()
      await page.waitForURL(`**/agent/${root}?member_session_id=${id}`)
      await page.getByRole('tab', { name, exact: true }).waitFor()
    }
  }
  await page.getByText('查看了本项目会话', { exact: true }).waitFor()
  const sessionSummary = page.locator('.tool-call-display.is-timeline.is-summary').filter({ has: page.getByText('查看了本项目会话', { exact: true }) })
  check(await sessionSummary.count() === 1, '真实聊天分组中的会话查询未复用通用工具摘要')
  check(!await page.locator('.event-list-toggle, .event-session-list').count(), '会话查询仍显示展开入口或名单')
  const results = page.locator('.cooperation-event:visible').filter({ has: page.locator('.event-action', { hasText: /^查看结果$/ }) })
  await results.getByRole('button', { name: 'factcheck', exact: true }).click()
  await page.locator('.panel-session-chat:visible').getByRole('button', { name: '由 quicksort 创建', exact: true }).waitFor()
  await page.locator('.panel-session-chat:visible').getByRole('button', { name: '由 quicksort 创建', exact: true }).click()
  await page.waitForURL(`**/agent/${root}?member_session_id=${child}`)
  await page.getByRole('button', { name: '最大化面板', exact: true }).click()
  await page.locator('.panel-session-chat:visible').getByRole('button', { name: '由 并行研究 创建', exact: true }).click()
  await page.waitForURL(`**/agent/${root}`)
  await page.locator('.panel-main-chat:visible').getByText('三个任务均已完成。排序分析、汇率资料和收益核验可以分别打开查看。', { exact: true }).waitFor()
  await page.goBack()
  await page.waitForURL(`**/agent/${root}?member_session_id=${child}`)
  await page.locator('.panel-session-chat:visible').getByRole('button', { name: '由 并行研究 创建', exact: true }).waitFor()
  await page.goForward()
  await page.waitForURL(`**/agent/${root}`)
  await page.locator('.panel-main-chat:visible').getByText('三个任务均已完成。排序分析、汇率资料和收益核验可以分别打开查看。', { exact: true }).waitFor()
  await page.getByRole('button', { name: '还原面板', exact: true }).click()
  await page.getByRole('button', { name: '隐藏侧边栏', exact: true }).click()
  await create.getByRole('button', { name: 'quicksort', exact: true }).click()
  await page.waitForURL(`**/agent/${root}?member_session_id=${child}`)
  await page.locator('.panel-session-chat:visible').getByRole('button', { name: '由 并行研究 创建', exact: true }).click()
  await page.waitForURL(`**/agent/${root}`)
  await page.waitForFunction(() => document.querySelector('.side-panel--file')?.getAttribute('aria-hidden') === 'true')
  check(!await page.locator('.side-panel--file.is-visible').count(), '根来源没有恢复主会话')
  await expand()
  await results.getByRole('button', { name: '研究总览', exact: true }).click()
  await page.waitForURL(`**/agent/${other}`)
  await page.getByRole('button', { name: '由 并行研究 创建', exact: true }).click()
  await page.waitForURL(`**/agent/${root}`)
  await expand()
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(false) })
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-events-light.png', animations: 'disabled' })
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(true) })
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-events-dark.png', animations: 'disabled' })
  for (const width of [1024, 768]) {
    await page.setViewportSize({ width, height: 1000 })
    check(await page.locator('.cooperation-event-row:visible').evaluateAll(rows => rows.every(row => row.getBoundingClientRect().right <= innerWidth + 1)), `事件行在 ${width}px 溢出`)
  }
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-events-768.png', animations: 'disabled' })
  edgeCase = true
  names['event-fx'] = '汇率研究与多市场风险核验的长标题会话名称用于检查省略和换行'
  await page.reload()
  await page.getByText('三个任务均已完成。排序分析、汇率资料和收益核验可以分别打开查看。', { exact: true }).waitFor()
  await expand()
  await create.getByRole('button', { name: 'quicksort', exact: true }).waitFor()
  check(!await page.locator('.cooperation-event').getByText('普通文件中的同名字段', { exact: true }).count(), '普通工具结果覆盖了协作身份')
  await page.locator('.event-error').getByText('操作失败', { exact: true }).waitFor()
  check(!await page.getByText('会话已归档，请选择其他目标。', { exact: true }).count(), '失败摘要泄露了原始错误正文')
  check(await page.locator('.cooperation-event-row:visible').evaluateAll(rows => rows.every(row => row.getBoundingClientRect().right <= innerWidth + 1)), '长标题或错误详情使事件行溢出')
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-events-error.png', animations: 'disabled' })
  page.off('pageerror', onError)
  check(!errors.length, `页面异常：${errors.join('; ')}`)
  return { groupedCreate: 3, groupedWait: 3, individualTargets: true, maximizedMemberSource: true, browserHistory: true, errorSummary: true, summaryOnlySessions: true, longName: true, thirdLevelSource: true, rootSource: true, crossRoot: true, widths: [1440, 1024, 768], themes: ['light', 'dark'], evidence: 'real DOM with deterministic HTTP fixture' }
}
