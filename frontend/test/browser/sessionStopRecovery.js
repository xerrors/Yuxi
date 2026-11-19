// 使用真实 Vite 页面与受控公开协议，检查停止请求等待态、终态和暂停提示。
// 执行：playwright-cli -s=stop-recovery run-code --filename=frontend/test/browser/sessionStopRecovery.js
// prettier-ignore
;async (page) => {
  await page.unrouteAll({ behavior: 'wait' })
  const threadId = '00000000-0000-4000-8000-000000000081'
  const agentId = 'stop-recovery-agent'
  const turnId = 'stop-turn', runId = 'stop-run'
  let stopped = false, waitingCancellation = false, requests = 0, releaseCancel, releaseStream
  const cancelGate = new Promise(resolve => { releaseCancel = resolve })
  const streamGate = new Promise(resolve => { releaseStream = resolve })
  const check = (value, message) => { if (!value) throw new Error(message) }
  const config = { model: 'replay:chat', tool_approval_mode: 'default', tools: [], skills: [] }
  const agent = {
    agent_id: agentId, slug: agentId, name: '停止恢复验证助手', description: '验证停止和队列恢复',
    config_json: { context: config }, configurable_items: {}, metadata: {}
  }
  const turn = () => ({
    id: turnId, object: 'agent.session.turn', session_id: threadId, status: stopped ? 'cancelled' : 'in_progress',
    yuxi: { current_run_id: runId, result_run_id: null, cancellation_pending: waitingCancellation,
      waitpoint: waitingCancellation ? { kind: 'answer' } : null,
      runs: [{ id: runId, turn_id: turnId, status: stopped ? 'cancelled' : waitingCancellation ? 'interrupted' : 'running' }], output: [] }
  })
  const thread = () => ({
    id: threadId, object: 'agent.session', status: stopped ? 'cancelled' : 'in_progress', agent: { id: agentId, model: config.model },
    created_at: 1791580000, metadata: {},
    yuxi: { title: '停止与队列恢复', current_turn: { id: turnId, status: turn().status, current_run_id: runId },
      tool_approval_mode: 'default', queue_paused: stopped, project_id: 'stop-project', config, is_pinned: false }
  })
  await page.addInitScript(() => localStorage.setItem('user_token', 'stop-recovery-fixture'))
  await page.route('**/api/**', async route => {
    const request = route.request()
    const raw = request.url(), path = raw.replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    let json
    if (path.endsWith('/events') && request.method() === 'GET') {
      await streamGate
      if (waitingCancellation) return route.fulfill({ contentType: 'text/event-stream', body: '' })
      const data = {
        type: 'agent.session.turn.cancelled', event_id: 'stop-done', session_id: threadId, turn_id: turnId,
        turn: { status: 'cancelled' }, yuxi: { run_id: runId, current_run_id: runId }
      }
      return route.fulfill({ contentType: 'text/event-stream', body: `id: 2.0.1.1-0.6\nevent: ${data.type}\ndata: ${JSON.stringify(data)}\n\n` })
    }
    if (path.endsWith('/events') && request.method() === 'POST') {
      check(request.postDataJSON().events[0].type === 'agent.session.input.cancel', '应提交取消当前轮次')
      requests++
      await cancelGate
      return route.fulfill({ status: 202, json: { object: 'yuxi.session.event.accepted', session_id: threadId, turn_id: turnId, run_id: runId, status: 'accepted' } })
    }
    if (path === '/api/auth/me') json = { id: 1, uid: 'stop-replay-user', username: '停止验证', role: 'user', department_id: 1 }
    else if (path === '/api/mention/plugins') json = []
    else if (path === '/api/agent') json = { agents: [agent] }
    else if (path === `/api/agent/${agentId}`) json = agent
    else if (path.endsWith(`/turns/${turnId}`)) json = turn()
    else if (path.endsWith('/items')) json = {
      data: [{ id: 'user-input', type: 'message', role: 'user', status: 'completed', turn_id: turnId,
        content: [{ type: 'input_text', text: '请处理当前任务' }], yuxi: { message_id: '1', run_id: runId, created_at: '2026-10-17T07:00:00Z' } }],
      has_more: false, last_id: 'user-input', yuxi: { runs: turn().yuxi.runs }
    }
    else if (path === '/api/v1/agents/sessions') json = { data: raw.includes('is_pinned=true') ? [] : [thread()], has_more: false }
    else if (path.endsWith(`/${threadId}`) || path.endsWith('/viewed')) json = thread()
    else if (path.endsWith('/queue')) json = { status: stopped ? 'paused' : 'running', queue_paused: stopped,
      inputs: stopped ? [{ input_id: 'old-input', status: 'pending', kind: 'follow_up', content: '保留的旧消息', attachment_status: 'ready' }] : [] }
    else if (path.endsWith('/inputs/old-input')) json = { input_id: 'old-input', status: 'pending' }
    else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    else if (path.endsWith('/attachments')) json = { files: [], attachments: [] }
    else if (path === '/api/projects') json = []
    else if (path.includes('/databases')) json = { databases: [] }
    else if (path.includes('/models')) json = { providers: [], models: [], data: [] }
    else json = { data: [], files: [], skills: [], projects: [], providers: [], config: {}, site_name: '语析', first_run: false }
    await route.fulfill({ json })
  })
  await page.goto('http://localhost:5173')
  await page.evaluate(() => localStorage.setItem('theme', 'light'))
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`http://localhost:5173/agent/${threadId}`)
  const stop = page.getByRole('button', { name: '停止回答', exact: true })
  await stop.waitFor()
  await stop.click()
  await page.getByText('正在停止，等待任务清理完成...', { exact: true }).waitFor()
  check(await stop.isDisabled(), '请求在途时停止按钮应禁用')
  check(requests === 1, '停止请求只发送一次')
  await page.screenshot({ path: '/tmp/yuxi-stop-pending-light.png' })
  releaseCancel()
  stopped = true
  releaseStream()
  await page.getByText('后续队列已暂停。发送新消息将优先处理并继续队列。', { exact: true }).waitFor()
  await stop.waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '继续队列', exact: true }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-stop-queue-light.png' })
  await page.evaluate(() => localStorage.setItem('theme', 'dark'))
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.reload()
  await page.getByText('后续队列已暂停。发送新消息将优先处理并继续队列。', { exact: true }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-stop-queue-dark.png' })
  stopped = false
  waitingCancellation = true
  await page.reload()
  await page.getByText('正在停止，等待任务清理完成...', { exact: true }).waitFor()
  check(await page.getByRole('button', { name: '停止回答', exact: true }).isDisabled(), '等待取消刷新后应保留停止状态')
  await page.screenshot({ path: '/tmp/yuxi-stop-wait-reconnect-dark.png' })
  await page.goto('about:blank')
  await page.unrouteAll({ behavior: 'wait' })
  return { requests, pending: 'visible', cancelled: 'settled', pausedQueue: 'visible', waitingReconnect: 'visible' }
}
