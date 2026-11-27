// 在开发前端运行：playwright-cli -s=coop-member-sync run-code --filename=frontend/test/browser/cooperationMemberSync.js
// 真实页面使用确定性 HTTP fixture；验证摘要到共享 runtime 的装配，不修改服务端数据。
// prettier-ignore
async (page) => {
  await page.unrouteAll({ behavior: 'wait' })
  const origin = page.url().split('/').slice(0, 3).join('/')
  const check = (value, message) => { if (!value) throw new Error(message) }
  const rootId = 'sync-root', childId = 'sync-child', agentId = 'default-chatbot'
  const agent = { agent_id: agentId, name: '协作同步助手', can_run: true, configurable_items: {}, config_json: { context: {} } }
  let stage = 'waiting', queueTexts = ['第一条外部排队输入'], streams = 0, summaries = 0, releaseStream, releaseOldInput
  let rootAdvanced = false, rootStatus = 'completed', releaseRootStream
  const streamGate = new Promise(resolve => { releaseStream = resolve })
  const rootStreamGate = new Promise(resolve => { releaseRootStream = resolve })
  const oldInputGate = new Promise(resolve => { releaseOldInput = resolve })
  const viewed = [], errors = []
  const onError = error => errors.push(error.message)
  page.on('pageerror', onError)
  const turn = id => {
    const root = id === rootId
    const next = root ? rootAdvanced : ['running', 'completed'].includes(stage)
    const turnId = `${id}-${next ? 'next' : 'first'}`
    const runId = `run-${turnId}`
    const status = root ? rootStatus : stage === 'completed' ? 'completed' : stage === 'waiting' ? 'requires_action' : stage === 'cancelled' ? 'cancelled' : 'in_progress'
    return { id: turnId, status, yuxi: { current_run_id: runId, output: [], runs: [],
      waitpoint: !root && stage === 'waiting' ? { id: 'sync-wait', run_id: runId, kind: 'answer', questions: [{ question_id: 'q', question: '协作任务需要你的回答' }] } : null } }
  }
  const session = id => ({ id, agent: { id: agentId }, status: turn(id).status,
    yuxi: { title: id === rootId ? '协作成员同步验证' : '资料整理', current_turn: turn(id), project_id: 'sync-project', tool_approval_mode: 'ask', parent_session_id: id === rootId ? null : rootId } })
  const summary = () => ({ sessions: [rootId, childId].map(id => ({ session_id: id,
    parent_session_id: id === rootId ? null : rootId, path: id === rootId ? '/root' : '/root/child', name: id === rootId ? 'root' : '资料整理',
    turn_id: turn(id).id, current_run_id: turn(id).yuxi.current_run_id,
    turn_status: turn(id).status === 'requires_action' ? 'waiting' : turn(id).status,
    waiting_for: turn(id).yuxi.waitpoint?.kind || null,
    queue_paused: id === childId && stage === 'cancelled', has_pending_input: id === childId && stage === 'cancelled' })) })
  const items = id => {
    if (id === rootId) return []
    const next = ['running', 'completed'].includes(stage)
    return [{ id: next ? 'new-task' : 'old-task', type: 'message', role: next ? 'user' : 'assistant', status: 'completed', turn_id: turn(id).id,
      content: [{ type: next ? 'input_text' : 'output_text', text: next ? '外部第二轮任务' : '等待中的原任务' }],
      yuxi: { message_id: next ? 2 : 1, run_id: turn(id).yuxi.current_run_id, created_at: next ? '2026-10-24T04:00:00Z' : '2026-10-24T03:00:00Z' } }]
  }
  await page.route('**/api/**', async route => {
    const request = route.request(), path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    const reply = json => route.fulfill({ json })
    if (path.endsWith('/cooperation')) { summaries++; return reply(summary()) }
    if (path.endsWith('/input-origins')) return reply({ origins: {} })
    const match = path.match(/\/sessions\/(sync-[^/]+)/)
    if (match) {
      const id = match[1]
      if (path.endsWith('/events') && request.method() === 'GET') {
        if (id === childId) streams++
        await (id === rootId ? rootStreamGate : streamGate)
        const current = turn(id)
        const data = { type: 'agent.session.turn.completed', session_id: id, turn_id: current.id,
          event_id: 'sync-done', turn: { status: 'completed' }, yuxi: { run_id: current.yuxi.current_run_id, current_run_id: current.yuxi.current_run_id } }
        return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify(data)}\n\n` })
      }
      if (path.endsWith('/viewed')) { viewed.push(id); return reply(session(id)) }
      if (path.endsWith('/items')) return reply({ data: items(id), has_more: false, last_id: items(id).at(-1)?.id || null, yuxi: { runs: [] } })
      if (path.includes('/turns/')) return reply(turn(id))
      if (path.endsWith('/state')) return reply({ agent_state: {}, interrupt: null })
      if (path.endsWith('/queue')) return reply({ status: stage === 'cancelled' && id === childId ? 'paused' : 'ready', queue_paused: stage === 'cancelled' && id === childId,
        inputs: stage === 'cancelled' && id === childId ? queueTexts.map((content, i) => ({ input_id: `queued-${i}`, status: 'pending', kind: 'follow_up', content })) : [] })
      if (path.includes('/inputs/')) {
        const inputId = path.split('/').at(-1)
        if (inputId === 'queued-0') {
          await oldInputGate
          return reply({ input_id: inputId, status: 'consumed', turn_id: `${childId}-first`, run_id: `run-${childId}-first`,
            items: [{ id: 'late-old-input', type: 'message', role: 'user', status: 'completed', turn_id: `${childId}-first`,
              content: [{ type: 'input_text', text: '旧轮次迟到的消费消息' }],
              yuxi: { input_id: inputId, message_id: 3, run_id: `run-${childId}-first` } }] })
        }
        return reply({ input_id: inputId, status: 'pending' })
      }
      if (path.endsWith('/attachments')) return reply({ files: [], attachments: [] })
      return reply(session(id))
    }
    if (path === '/api/v1/agents/sessions') return reply({ data: request.url().includes('is_pinned=true') ? [] : [session(rootId)], has_more: false })
    if (path === '/api/mention/resources') return reply({})
    if (path === '/api/agent') return reply({ agents: [agent] })
    if (path === `/api/agent/${agentId}`) return reply(agent)
    if (path === '/api/auth/me' || path.endsWith('/users/me')) return reply({ id: 1, username: '协作验证用户', uid: 'sync-fixture-user', role: 'user' })
    if (path === '/api/projects') return reply([{ id: 'sync-project', name: '同步验证', workdir_path: '/fixture', threads: [] }])
    if (path.includes('/filesystem/tree')) return reply({ tree: [] })
    return reply({ data: [], files: [], databases: [], providers: [], models: {}, first_run: false, enabled: false })
  })
  await page.addInitScript(() => localStorage.setItem('user_token', 'member-sync-browser-fixture'))
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${origin}/agent/${rootId}`)
  await page.evaluate(async () => {
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    useThemeStore().setTheme(false)
  })
  await page.locator('.attention-pill').click()
  const member = page.locator('.panel-session-chat:visible')
  await member.getByRole('dialog').waitFor()
  await member.getByRole('heading', { name: '协作任务需要你的回答', exact: true }).waitFor()
  const answer = member.getByRole('dialog').getByRole('textbox')
  await answer.fill('成员的未提交答案')
  await page.getByRole('tab', { name: '开始', exact: true }).click()
  await page.getByRole('button', { name: '工作区文件', exact: true }).click()
  await page.getByRole('tab', { name: /^资料整理/ }).click()
  check(await answer.inputValue() === '成员的未提交答案', '切换成员标签不应清除当前等待点的答案')
  await page.getByRole('button', { name: '最大化面板', exact: true }).click()
  const rootViewedBeforeCompletion = viewed.filter(id => id === rootId).length
  const rootSubscription = page.waitForRequest(request => request.url().includes(`/sessions/${rootId}/events`))
  rootAdvanced = true
  rootStatus = 'in_progress'
  await rootSubscription
  rootStatus = 'completed'
  releaseRootStream()
  await page.waitForFunction(async () => {
    const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
    const root = useSessionRuntimeStore().peekThreadState('sync-root')
    return root?.turnStatus === 'completed' && root.detail?.yuxi.current_turn?.id === 'sync-root-next'
  })
  await page.waitForLoadState('networkidle')
  check(viewed.filter(id => id === rootId).length === rootViewedBeforeCompletion, '最大化成员时隐藏的主会话不能标记已读')
  await page.getByRole('button', { name: '还原面板', exact: true }).click()
  // 初始等待态没有 SSE；先结束前台恢复，避免将它的迟到结果误算为后台新读。
  await page.waitForLoadState('networkidle')
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const beforeSummaries = summaries
  stage = 'cancelled'
  await page.waitForTimeout(6500)
  check(summaries === beforeSummaries, '浏览器后台页面仍在轮询协作摘要')
  check(await member.getByRole('dialog').isVisible(), '浏览器后台页面不应发起成员对账')
  check(await answer.inputValue() === '成员的未提交答案', '切换浏览器标签不应清除当前答案')
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await member.getByRole('dialog').waitFor({ state: 'hidden' })
  await member.getByText('后续队列已暂停。发送新消息将优先处理并继续队列。', { exact: true }).waitFor()
  queueTexts.push('第二条外部排队输入')
  await member.getByText('第二条外部排队输入', { exact: true }).waitFor()
  check(await member.getByRole('textbox', { name: /问点什么/ }).isEditable(), '外部取消后旧等待仍然禁止普通输入')
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-sync-cancelled.png', animations: 'disabled' })
  await page.getByRole('tab', { name: '工作区文件', exact: true }).click()
  const beforeViewed = viewed.filter(id => id === childId).length
  stage = 'running'
  await page.waitForFunction(async () => {
    const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
    return useSessionRuntimeStore().getThreadState('sync-child')?.activeRunId === 'run-sync-child-next'
  })
  check(viewed.filter(id => id === childId).length === beforeViewed, '隐藏成员对账错误标记已读')
  check(await page.getByRole('tab', { name: '工作区文件', exact: true }).getAttribute('aria-selected') === 'true', '后台成员更新抢走活动标签')
  await page.getByRole('tab', { name: '资料整理', exact: true }).click()
  await member.getByText('外部第二轮任务', { exact: true }).waitFor()
  check(streams === 1, '新轮次应该只建立一个 SSE')
  releaseOldInput()
  await member.getByText('旧轮次迟到的消费消息', { exact: true }).waitFor()
  await page.waitForFunction(async () => {
    const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
    const state = useSessionRuntimeStore().getThreadState('sync-child')
    return state?.activeRunId === 'run-sync-child-next' && !state.inputQueueMonitor?.inputIds.has('queued-0')
  })
  const beforeUnchangedSummary = summaries
  await page.waitForTimeout(3200)
  check(summaries > beforeUnchangedSummary, '未执行相同摘要的后续刷新')
  check(await member.getByText('外部第二轮任务', { exact: true }).isVisible(), '迟到消费响应丢失新轮次内容')
  check(streams === 1, '旧 Input 迟到消费不能替换当前 SSE')
  await page.evaluate(async () => {
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    useThemeStore().setTheme(true)
  })
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-sync-next-dark.png', animations: 'disabled' })
  stage = 'completed'
  releaseStream()
  await member.getByRole('button', { name: '停止回答', exact: true }).waitFor({ state: 'hidden' })
  await page.setViewportSize({ width: 768, height: 900 })
  await page.locator('.agent-view').screenshot({ path: '/tmp/yuxi-cooperation-sync-narrow.png', animations: 'disabled' })
  page.off('pageerror', onError)
  check(!errors.length, `页面异常：${errors.join('; ')}`)
  return { externalCancellation: true, pausedQueue: true, secondQueuedInput: true, nextTurnHistory: true, lateOldInputKeepsCurrentTurn: true, oneSse: streams === 1,
    hiddenRootDoesNotPoll: true, hiddenRootCompletionDoesNotMarkViewed: true, hiddenMemberDoesNotMarkViewed: true, hiddenMemberDoesNotSelectTab: true, screenshots: ['cancelled', 'next-dark', 'narrow'], evidence: 'real page with deterministic HTTP fixture' }
}
