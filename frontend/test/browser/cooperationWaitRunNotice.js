// 在打开本地 Vite 的独立浏览器执行，使用协议 fixture 验证实际对话页面。
// prettier-ignore
async page => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const threadId = '00000000-0000-4000-8000-000000000083'
  const turnId = 'cooperation-turn', runId = 'cooperation-wait', resumeId = 'cooperation-resume'
  const waitpoint = { id: 'cooperation-waitpoint', kind: 'cooperation', run_id: runId }
  const agent = { agent_id: 'default-chatbot', name: '测试助手', can_run: true, configurable_items: {}, config_json: { context: {} } }
  const waitRun = { id: runId, turn_id: turnId, status: 'interrupted', error_type: 'cooperation_waiting', run_type: 'chat', timing: { created_at: '2026-11-26T10:00:00Z' } }
  const waitMessage = '已派发协作任务，等待结果。'
  const finalMessage = '协作已完成，结果已汇总。'
  const followUp = '新一轮任务已完成。'
  let phase = 'waiting'
  let snapshotUnavailable = false
  const turn = () => {
    const completed = phase === 'completed'
    const resumed = completed || phase === 'resuming'
    const runs = [waitRun]
    if (resumed) runs.push({ id: resumeId, turn_id: turnId, status: completed ? 'completed' : 'running', run_type: 'resume' })
    return {
      id: turnId, status: completed ? 'completed' : ['cancelled', 'next-turn'].includes(phase) ? 'cancelled' : 'in_progress',
      yuxi: {
        current_run_id: resumed ? resumeId : runId, result_run_id: completed ? resumeId : null,
        waitpoint: phase === 'waiting' ? waitpoint : null, runs,
        output: completed ? [message(resumeId, finalMessage, 'final_answer')] : []
      }
    }
  }
  const message = (id, text, contentPhase, owner = turnId) => ({
    id: `message-${id}`, type: 'message', role: 'assistant', status: 'completed', phase: contentPhase,
    turn_id: owner, content: [{ type: 'output_text', text }],
    yuxi: { message_id: `message-${id}`, run_id: id, created_at: id === runId ? '2026-11-26T10:00:00Z' : '2026-11-26T10:01:00Z' }
  })
  const nextTurnId = 'follow-up-turn', nextRunId = 'follow-up-run'
  const nextTurn = { id: nextTurnId, status: 'completed', yuxi: {
    current_run_id: nextRunId, result_run_id: nextRunId, waitpoint: null,
    runs: [{ id: nextRunId, turn_id: nextTurnId, status: 'completed', run_type: 'chat' }],
    output: [message(nextRunId, followUp, 'final_answer', nextTurnId)]
  } }
  const priorTurnId = 'prior-cooperation-turn', priorWaitId = 'prior-wait', priorResumeId = 'prior-resume'
  const priorAnswer = '历史协作已续跑并完成。'
  const priorTurn = { id: priorTurnId, status: 'completed', yuxi: {
    current_run_id: priorResumeId, result_run_id: priorResumeId, waitpoint: null,
    runs: [
      { ...waitRun, id: priorWaitId, turn_id: priorTurnId },
      { id: priorResumeId, turn_id: priorTurnId, status: 'completed', run_type: 'resume', resume_from_run_id: priorWaitId }
    ],
    output: [message(priorResumeId, priorAnswer, 'final_answer', priorTurnId)]
  } }
  const thread = () => {
    const current = phase === 'next-turn' ? nextTurn : turn()
    return { id: threadId, status: current.status, agent: { id: agent.agent_id, model: 'fixture:chat' }, created_at: 1795687200,
      yuxi: { title: '协作等待验证', current_turn: { id: current.id, status: current.status, current_run_id: current.yuxi.current_run_id, waitpoint: current.yuxi.waitpoint }, tool_approval_mode: 'default' } }
  }
  const streams = []
  const finishStream = (status, id) => {
    const data = { type: `agent.session.turn.${status}`, session_id: threadId, turn_id: turnId,
      turn: { id: turnId, status }, yuxi: { run_id: id, current_run_id: id,
        error_type: status === 'cancelled' ? 'cooperation_waiting' : null } }
    streams.at(-1)(`id: 2.0.1.1-0.6\nevent: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`)
  }
  await page.addInitScript(() => {
    if (location.protocol === 'http:') localStorage.setItem('user_token', 'cooperation-wait-fixture')
  })
  let readPriorTurn = false
  await page.route('**/api/**', async route => {
    const pathname = route.request().url().slice(origin.length).split('?')[0]
    const reply = json => route.fulfill({ json })
    if (snapshotUnavailable && (pathname.endsWith(`/sessions/${threadId}`) || pathname.endsWith(`/turns/${turnId}/items`))) {
      return route.fulfill({ status: 503, json: { detail: '会话快照暂不可用' } })
    }
    if (pathname.endsWith(`/sessions/${threadId}/events`)) {
      const body = await new Promise(resolve => { streams.push(resolve) })
      return route.fulfill({ contentType: 'text/event-stream', body })
    }
    if (pathname.endsWith('/items')) {
      const { output, runs } = turn().yuxi
      const data = [...output, message(runId, waitMessage, 'commentary')]
      if (phase === 'next-turn') {
        data.unshift(...nextTurn.yuxi.output, ...priorTurn.yuxi.output, message(priorWaitId, '历史等待段。', 'commentary', priorTurnId))
        runs.push(...nextTurn.yuxi.runs, ...priorTurn.yuxi.runs)
      }
      return reply({ data, has_more: false, yuxi: { runs } })
    }
    if (pathname.endsWith(`/turns/${turnId}`)) return reply(turn())
    if (pathname.endsWith(`/turns/${nextTurn.id}`)) return reply(nextTurn)
    if (pathname.endsWith(`/turns/${priorTurnId}`)) {
      readPriorTurn = true
      return reply(priorTurn)
    }
    if (pathname.endsWith(`/sessions/${threadId}`) || pathname.endsWith('/viewed')) return reply(thread())
    if (pathname === '/api/v1/agents/sessions') return reply({ data: [thread()], has_more: false })
    if (pathname.endsWith('/queue')) return reply({ inputs: [], status: 'ready' })
    if (pathname.endsWith('/cooperation')) return reply({ sessions: [] })
    if (pathname === '/api/system/info') return reply({ success: true, data: { branding: { name: 'Yuxi' } } })
    if (pathname === '/api/auth/me') return reply({ id: 1, uid: 'cooperation-wait-user', username: '测试用户', role: 'user' })
    if (pathname === '/api/agent') return reply({ agents: [agent] })
    if (pathname === `/api/agent/${agent.agent_id}`) return reply(agent)
    if (pathname === '/api/projects' || pathname === '/api/mention/plugins') return reply([])
    return reply({ data: [], databases: [], models: {}, first_run: false, enabled: false })
  })
  const waiting = page.getByText('等待协作任务完成...', { exact: true })
  const interrupted = page.getByText('本次运行已中断', { exact: true })
  const checkWaiting = async () => {
    await page.getByText(waitMessage, { exact: true }).waitFor()
    await waiting.waitFor()
    if (await interrupted.count()) throw new Error('协作等待同时显示「本次运行已中断」')
  }
  const reopenStreaming = async () => {
    const restoredStream = page.waitForRequest(request => request.url().includes(`/sessions/${threadId}/events`))
    await page.reload()
    await restoredStream
  }
  try {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/agent/${threadId}`)
    await checkWaiting()
    await reopenStreaming()
    await checkWaiting()
    phase = 'resuming'
    await reopenStreaming()
    await page.getByText('正在生成回复...', { exact: true }).waitFor()
    if (await interrupted.count()) throw new Error('续跑还没有输出时误报前一段等待已中断')
    phase = 'completed'
    finishStream('completed', resumeId)
    await page.getByText(finalMessage, { exact: true }).waitFor()
    await waiting.waitFor({ state: 'hidden' })
    if (await interrupted.count()) throw new Error('同一轮恢复后仍显示历史等待段的中断提示')
    const cancelled = page.getByText('本次运行已取消', { exact: true })
    phase = 'waiting'
    await reopenStreaming()
    await checkWaiting()
    snapshotUnavailable = true
    phase = 'cancelled'
    const snapshotFailures = Promise.all([`/sessions/${threadId}`, `/turns/${turnId}/items`].map(path =>
      page.waitForResponse(response => response.status() === 503 && response.url().split('?')[0].endsWith(path))
    ))
    finishStream('cancelled', runId)
    await snapshotFailures
    await cancelled.waitFor({ timeout: 5000 })
    snapshotUnavailable = false
    await page.reload()
    await cancelled.waitFor()
    if (await interrupted.count()) throw new Error('已取消的等待轮次仍显示中断提示')
    phase = 'next-turn'
    await page.reload()
    await page.getByText(followUp, { exact: true }).waitFor()
    await page.getByText(waitMessage, { exact: true }).waitFor()
    await page.getByText(priorAnswer, { exact: true }).waitFor()
    await cancelled.waitFor({ timeout: 5000 })
    if (await interrupted.count()) throw new Error('历史取消的轮次显示中断提示')
    if (readPriorTurn) throw new Error('存在明确续跑的历史等待段重复读取了轮次详情')
    return { waiting: true, refreshed: true, resumed: true, cancelled: true, cancelledHistory: true }
  } finally {
    for (const release of streams) release('')
    await page.goto('about:blank')
    await page.unrouteAll({ behavior: 'wait' })
  }
}
