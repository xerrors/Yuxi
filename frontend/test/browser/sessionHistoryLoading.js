// 确定性公开 API replay，使用真实 Vite 页面验证历史窗口、滚动锚点与重试。
// 执行：playwright-cli -s=history-loading run-code --filename=frontend/test/browser/sessionHistoryLoading.js
// prettier-ignore
async (page) => {
  await page.unrouteAll({ behavior: 'wait' })
  const origin = page.url().split('/').slice(0, 3).join('/')
  const threadId = '00000000-0000-4000-8000-000000000001'
  const agentId = '00000000-0000-4000-8000-000000000002'
  const thread = {
    id: threadId, object: 'agent.session', status: 'completed', agent: { id: agentId },
    created_at: 1791580000, metadata: {},
    yuxi: { title: '历史消息加载验证', current_turn: null, context: {}, project_id: 'history-project', config: {}, is_pinned: false }
  }
  const agent = { agent_id: agentId, name: '历史验证助手', description: '用于历史阅读验证', config_json: { context: {} }, configurable_items: {}, metadata: {} }
  let longRun = false
  const item = id => ({
    id: String(id), object: 'agent.session.item', type: 'message',
    role: longRun ? 'user' : id % 2 ? 'user' : 'assistant', status: 'completed', turn_id: longRun ? 'turn-long' : `turn-${Math.ceil(id / 2)}`,
    content: [{ type: longRun || id % 2 ? 'input_text' : 'output_text', text: `历史消息 ${String(id).padStart(4, '0')}：这是一条用于验证阅读位置的消息。` }],
    yuxi: { message_id: String(id), run_id: longRun ? 'run-long' : `run-${Math.ceil(id / 2)}`, created_at: new Date(1791580000000 + id * 1000).toISOString() }
  })
  const requests = [], unknown = new Set()
  let failNext = true, delayed = false, release
  let failAgent = false
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  await page.addInitScript(() => localStorage.setItem('user_token', 'history-replay-token'))
  await page.route('**/api/**', async route => {
    const raw = route.request().url()
    const path = raw.replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    const url = { searchParams: { get: name => decodeURIComponent(raw.match(new RegExp(`[?&]${name}=([^&]*)`))?.[1] || '') || null } }
    let json
    if (path === '/api/auth/me') json = { id: 1, uid: 'history-verification', username: '历史验证', role: 'user' }
    else if (path === '/api/agent') json = { agents: [agent] }
    else if (path === `/api/agent/${agentId}`) {
      if (failAgent) return route.fulfill({ status: 503, json: { detail: '智能体暂不可用' } })
      json = agent
    }
    else if (path.endsWith('/items')) {
      const after = url.searchParams.get('after'), limit = Number(url.searchParams.get('limit'))
      requests.push({ after, limit })
      if (delayed) { delayed = false; await new Promise(resolve => { release = resolve }) }
      if (failNext) { failNext = false; return route.fulfill({ status: 503, json: { detail: '历史读取暂不可用' } }) }
      const newest = after ? Number(after) - 1 : 950
      const data = Array.from({ length: Math.min(limit, newest) }, (_, index) => item(newest - index))
      json = { data, last_id: data.at(-1)?.id || null, has_more: newest > limit,
        yuxi: { runs: [...new Set(data.map(item => item.yuxi.run_id))].map(id => ({ id, turn_id: id.replace('run-', 'turn-'), status: 'completed', artifacts: [] })) } }
    } else if (path === '/api/v1/agents/sessions') json = { data: url.searchParams.get('is_pinned') === 'true' ? [] : [thread], has_more: false }
    else if (path.endsWith(`/${threadId}`) || path.endsWith('/viewed')) json = thread
    else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    else if (path.endsWith('/queue')) json = { inputs: [], status: 'ready' }
    else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    else if (path.endsWith('/attachments')) json = { files: [], attachments: [] }
    else if (path === '/api/projects') json = []
    else if (path.includes('/databases')) json = { databases: [] }
    else if (path.includes('/models')) json = { providers: [], models: [], data: [] }
    else { unknown.add(path); json = { data: [], files: [], skills: [], projects: [], providers: [], config: {}, site_name: '语析', first_run: false } }
    await route.fulfill({ json })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${origin}/agent/${threadId}`)
  const chat = page.locator('.agent-view .chat-main')
  await page.getByRole('button', { name: '重新读取对话', exact: true }).waitFor()
  failAgent = true
  requests.length = 0 // 首读失败没有产生历史；后续窗口 oracle 从真实重试开始计数。
  await page.getByRole('button', { name: '重新读取对话', exact: true }).click()
  await page.getByText('智能体加载失败，请重试', { exact: true }).waitFor()
  check(requests.length === 3, `历史重试首次窗口请求数 ${requests.length}`)
  requests.length = 0 // 显式重新选择会话复核权限；后续分页以本次刷新为基线。
  failAgent = false
  await page.locator('.agent-availability-notice').getByRole('button', { name: '重试', exact: true }).click()
  await page.locator('.agent-view [contenteditable="true"]').waitFor()
  await page.locator('.agent-view [contenteditable="true"]').fill('历史重试后可以编辑')
  check(await page.getByRole('button', { name: '发送消息', exact: true }).isEnabled(), '首次历史重试后输入初始化没有恢复')
  await page.locator('.agent-view [contenteditable="true"]').fill('')
  await page.getByText('历史消息 0950：', { exact: false }).waitFor()
  await page.waitForFunction(() => document.querySelector('.agent-view .chat-main')?.scrollTop > 600)
  await page.waitForTimeout(300)
  check(requests.length === 3, `首次窗口请求数 ${requests.length}`)
  const bubbleAlignment = await chat.locator('.message-box.human').first().evaluate(el => ({
    right: el.getBoundingClientRect().right, parentRight: el.parentElement.getBoundingClientRect().right,
    width: el.getBoundingClientRect().width, parentWidth: el.parentElement.getBoundingClientRect().width
  }))
  check(bubbleAlignment.width < bubbleAlignment.parentWidth && Math.abs(bubbleAlignment.right - bubbleAlignment.parentRight) < 2, '用户气泡没有保持右对齐')
  check(await page.getByText('历史消息 0651：', { exact: false }).count() === 1, '初次没有读取300条')
  delayed = true
  await chat.evaluate(el => { el.scrollTop = 500 })
  await page.getByRole('button', { name: '正在加载更早消息…', exact: true }).waitFor({ state: 'attached' })
  await chat.evaluate(el => {
    const group = [...el.querySelectorAll('.history-display-item')].find(node => node.getBoundingClientRect().bottom > el.getBoundingClientRect().top)
    group.dataset.historyAnchor = 'true'
  })
  await chat.evaluate(el => { el.scrollTop = 0 })
  const movedTop = await page.locator('[data-history-anchor]').evaluate(el => el.getBoundingClientRect().top)
  await page.screenshot({ path: '/tmp/yuxi-history-loading.png' })
  check(requests.length === 4, '在途追加产生重复请求')
  release()
  await page.getByText('历史消息 0351：', { exact: false }).waitFor()
  await page.getByRole('button', { name: '加载更早消息', exact: true }).waitFor({ state: 'attached' })
  await page.waitForTimeout(200)
  const restoredTop = await page.locator('[data-history-anchor]').evaluate(el => el.getBoundingClientRect().top)
  check(Math.abs(restoredTop - movedTop) < 2, `阅读位置偏移 ${restoredTop - movedTop}px`)
  check(requests.length === 6, `追加请求数 ${requests.length}`)
  await page.screenshot({ path: '/tmp/yuxi-history-loaded.png' })
  failNext = true
  await chat.evaluate(el => { el.scrollTop = 400 })
  await page.getByRole('button', { name: '加载失败，点击重试', exact: true }).waitFor({ state: 'attached' })
  await chat.evaluate(el => { el.scrollTop = 200 })
  await page.waitForTimeout(200)
  check(requests.length === 7, '失败后发生自动重试')
  await chat.evaluate(el => { el.scrollTop = 0 })
  await page.screenshot({ path: '/tmp/yuxi-history-error.png' })
  await page.getByRole('button', { name: '加载失败，点击重试', exact: true }).click()
  await page.getByText('历史消息 0051：', { exact: false }).waitFor()
  await page.getByRole('button', { name: '加载更早消息', exact: true }).waitFor({ state: 'attached' })
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.setViewportSize({ width: 768, height: 960 })
  await page.waitForTimeout(400) // 等宽度重排完成，再模拟用户上滚。
  await chat.evaluate(el => { el.scrollTop = 300 })
  await page.getByText('历史消息 0001：', { exact: false }).waitFor()
  await page.waitForFunction(() => !document.querySelector('.history-load-btn'))
  check(requests.length === 11, `末页没有停止读取: ${requests.length}`)
  await page.locator('.ant-message-notice').waitFor({ state: 'hidden' })
  await page.screenshot({ path: '/tmp/yuxi-history-dark-768.png' })
  const completedRequests = [...requests]
  longRun = true
  requests.length = 0
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.evaluate(() => document.documentElement.classList.remove('dark'))
  await page.goto(`${origin}/agent/${threadId}`)
  await page.getByText('历史消息 0950：', { exact: false }).waitFor()
  await page.waitForFunction(() => document.querySelector('.agent-view .chat-main')?.scrollTop > 600)
  await page.waitForTimeout(300)
  check(await chat.locator('.group-box').count() === 1, '长Run用例没有合并到同一消息组')
  delayed = true
  await chat.evaluate(el => { el.scrollTop = 500 })
  await page.getByRole('button', { name: '正在加载更早消息…', exact: true }).waitFor({ state: 'attached' })
  const longAnchor = await chat.evaluate(el => {
    const item = [...el.querySelectorAll('.history-display-item')].find(node => node.getBoundingClientRect().bottom > el.getBoundingClientRect().top)
    return { key: item.dataset.historyKey, top: item.getBoundingClientRect().top }
  })
  release()
  await page.getByText('历史消息 0351：', { exact: false }).waitFor()
  await page.getByRole('button', { name: '加载更早消息', exact: true }).waitFor({ state: 'attached' })
  await page.waitForTimeout(200)
  const longTop = await chat.evaluate((el, key) => [...el.querySelectorAll('.history-display-item')].find(node => node.dataset.historyKey === key).getBoundingClientRect().top, longAnchor.key)
  check(Math.abs(longTop - longAnchor.top) < 2, `同Run内部追加阅读位置偏移 ${longTop - longAnchor.top}px`)
  check(await chat.locator('.group-box').count() === 1, '追加拆散了长Run')
  const inputId = 'accepted-input', turnId = 'accepted-turn', runId = 'accepted-run'
  const history = item(950)
  const human = { ...item(951), id: 'accepted-human', role: 'user', turn_id: turnId,
    content: [{ type: 'input_text', text: '冻结输入内容' }], yuxi: { run_id: runId, input_id: inputId, message_id: 951, output_index: 0, created_at: '2026-10-16T00:10:00Z' } }
  const answer = { ...item(952), id: 'answer', role: 'assistant', turn_id: turnId, phase: 'final_answer',
    content: [{ type: 'output_text', text: '唯一流的最终回答' }], yuxi: { run_id: runId, message_id: 952, output_index: 1, created_at: '2026-10-16T00:10:01Z' } }
  let sends = 0, receipts = 0, streams = 0, frozen, completed = false
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    let json
    if (path.endsWith('/events') && request.method() === 'POST') {
      sends++
      const data = { body: request.postDataJSON(), key: request.headers()['idempotency-key'] }
      if (sends === 1) {
        frozen = data
        return route.fulfill({ status: 503, json: { detail: '接收结果未知' } })
      }
      check(JSON.stringify(data) === JSON.stringify(frozen), '重试改变了冻结载荷或请求身份')
      json = { input_id: inputId }
    } else if (path.endsWith('/receipt')) {
      receipts++
      return route.fulfill({ status: 404, json: { detail: '回执不存在' } })
    } else if (path.endsWith(`/inputs/${inputId}`)) {
      json = { input_id: inputId, status: 'consumed', run_id: runId, turn_id: turnId, items: [human] }
    } else if (path.endsWith('/events')) {
      streams++
      const events = [
        { type: 'agent.session.turn.item.added', item: { ...answer, status: 'in_progress', content: [] } },
        { type: 'agent.session.turn.output_text.delta', item_id: answer.id, content_index: 0, delta: '唯一流的最终回答' },
        { type: 'agent.session.turn.item.done', item: answer },
        { type: 'yuxi.session.turn.state', turn: { status: 'completed' }, yuxi: { run_id: runId, current_run_id: runId } }
      ].map((event, index) => ({ ...event, event_id: `input-event-${index}`, session_id: threadId, turn_id: turnId, yuxi: { run_id: runId, ...event.yuxi } }))
      completed = true
      return route.fulfill({ contentType: 'text/event-stream', body: events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('') })
    } else if (path.endsWith('/items') && completed) {
      json = { data: [answer, human, history], has_more: false, last_id: history.id, yuxi: { runs: [] } }
    } else if (path.endsWith(`/${threadId}`) || path.endsWith('/viewed')) {
      json = { ...thread, yuxi: { ...thread.yuxi, current_turn: sends >= 2 ? { id: turnId, status: completed ? 'completed' : 'in_progress' } : null } }
    } else if (path.endsWith(`/turns/${turnId}`)) {
      json = { id: turnId, status: completed ? 'completed' : 'in_progress', yuxi: { current_run_id: runId, result_run_id: runId, output: [human, answer], runs: [{ id: runId, turn_id: turnId, status: 'completed', artifacts: [] }] } }
    } else if (path.endsWith(`/runs/${runId}`)) {
      json = { id: runId, turn_id: turnId, status: 'completed', output: [answer] }
    } else if (path.endsWith('/references')) {
      json = { turn_id: turnId, available: false, sources: [], references: null }
    } else if (path.endsWith('/input-origins')) {
      json = { origins: {} }
    } else if (path.endsWith('/feedback')) {
      json = { items: [] }
    } else return route.fallback()
    await route.fulfill({ json })
  })
  const editor = page.locator('.agent-view [contenteditable="true"]')
  await editor.fill('冻结输入内容')
  for (const inputMethod of [{ isComposing: true }, { keyCode: 229 }]) {
    await editor.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', ...inputMethod })
    await page.waitForTimeout(50)
    check(sends === 0 && await editor.innerText() === '冻结输入内容', '输入法确认候选词误发消息或清掉草稿')
  }
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  await page.getByText('发送结果待确认，重试会确认同一条消息。').waitFor()
  await editor.fill('用户继续编辑的新草稿')
  await page.getByRole('button', { name: '重试发送', exact: true }).click()
  await page.getByText('唯一流的最终回答', { exact: true }).waitFor()
  check(sends === 2 && receipts === 1 && streams === 1, `流程重复：${JSON.stringify({ sends, receipts, streams })}`)
  await page.waitForFunction(async id => {
    const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
    const runtime = useSessionRuntimeStore()
    const state = runtime.getThreadState(id)
    return !state.isStreaming && !state.activeRunId && !runtime.historyPages[id]?.loading
  }, threadId)
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  check(await editor.innerText() === '用户继续编辑的新草稿', '接收旧提交清掉了新草稿')
  check(await page.getByText('历史消息 0950：', { exact: false }).count() === 1, '发送或终态重读丢失/复制历史')
  check(await page.getByText('冻结输入内容', { exact: true }).count() === 1, '乐观消息与正式输入重复展示')
  await page.getByText('唯一流的最终回答', { exact: true }).scrollIntoViewIfNeeded()
  await page.waitForTimeout(3500)
  await page.screenshot({ path: '/tmp/yuxi-session-input-lifecycle.png' })
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.setViewportSize({ width: 768, height: 960 })
  await page.getByText('唯一流的最终回答', { exact: true }).scrollIntoViewIfNeeded()
  await page.waitForTimeout(300)
  await page.screenshot({ path: '/tmp/yuxi-session-input-lifecycle-dark.png' })
  // 真实页面公开路由入口必须重新核对已缓存 Thread 的可见性。
  let publicFailure = 403, rechecks = 0
  await page.route(`**/api/v1/agents/sessions/${threadId}`, route => {
    rechecks++
    return publicFailure === 'network' ? route.abort('failed') : route.fulfill({ status: publicFailure, json: { detail: '路由复核失败' } })
  })
  for (const status of [403, 404, 410, 503, 'network']) {
    publicFailure = status
    const result = await page.locator('.agent-session-workspace').first().evaluate(async (el, id) => {
      let owner = el.__vueParentComponent
      while (owner && !owner.exposed?.selectThreadFromRoute) owner = owner.parent
      if (!owner) throw new Error('缺少真实 Workspace 路由入口')
      return owner.exposed.selectThreadFromRoute(id)
    }, threadId)
    check(result === ([403, 404, 410].includes(status) ? false : null), `缓存Thread复核${status}错误分类失效：${result}`)
  }
  check(rechecks >= 5, '已缓存Thread跳过服务端可见性复核')
  return { initialItems: 300, appendedItems: 300, anchorShift: restoredTop - movedTop, sameRunAnchorShift: longTop - longAnchor.top, totalItems: 950, requests: completedRequests, loading: true, retry: true, firstHistoryRetryRestoresInput: true, agentFailureRetryRestoresInput: true, cachedRouteRevalidation: true, dark768: true, input: { sends, receipts, streams, compositionGuard: true, nextDraftRetained: true }, unknown: [...unknown] }
}
