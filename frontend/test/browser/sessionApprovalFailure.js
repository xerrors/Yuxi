// 在独立开发浏览器运行：playwright-cli run-code --filename=frontend/test/browser/sessionApprovalFailure.js
// HTTP fixture 控制请求失败顺序；不写入真实服务端数据。
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const agent = { agent_id: 'default-chatbot', name: '智能助手', can_run: true, configurable_items: {}, config_json: { context: {} } }
  let phase = 'old', failNextTurn = false, releaseFailure, onRequestHeld
  const turn = () => ({
    id: 'approval-turn', status: phase === 'running' ? 'in_progress' : 'requires_action',
    yuxi: {
      current_run_id: phase === 'old' ? 'run-A' : 'run-B', output: [], runs: [],
      waitpoint: phase === 'running' ? null : {
        id: phase === 'old' ? 'wait-A' : 'wait-B',
        run_id: phase === 'old' ? 'run-A' : 'run-B', kind: 'answer',
        questions: [{ question_id: 'question', question: phase === 'old' ? '是否继续旧任务？' : '请确认新的任务？', allow_other: true }]
      }
    }
  })
  const session = () => ({ id: 'approval-thread', agent: { id: agent.agent_id }, status: turn().status,
    yuxi: { title: '审批失败竞态验证', current_turn: turn() } })
  const input = {
    id: 'approval-input', type: 'message', role: 'user', status: 'completed', turn_id: 'approval-turn',
    content: [{ type: 'input_text', text: '准备报告，执行前请确认。' }],
    yuxi: { message_id: 'approval-input', run_id: 'run-A', created_at: '2026-11-25T10:00:00Z' }
  }
  await page.route('**/api/**', async route => {
    const request = route.request(), path = request.url().split('?')[0].replace(/^https?:\/\/[^/]+/, '')
    const reply = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (path.includes('/sessions/approval-thread')) {
      if (path.endsWith('/items')) return reply({
        data: [input], has_more: false, last_id: input.id,
        yuxi: { runs: [{ id: 'run-A', turn_id: input.turn_id, status: 'interrupted', artifacts: [] }] }
      })
      if (path.includes('/turns/')) {
        if (failNextTurn) {
          failNextTurn = false
          await new Promise(resolve => { releaseFailure = resolve; onRequestHeld() })
          return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'fixture delayed failure' }) })
        }
        return reply(turn())
      }
      if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: '' })
      if (path.endsWith('/state')) return reply({ agent_state: {} })
      if (path.endsWith('/queue')) return reply({ inputs: [], status: 'idle' })
      if (path.endsWith('/attachments')) return reply({ attachments: [] })
      if (path.endsWith('/cooperation')) return reply({ sessions: [] })
      return reply(session())
    }
    if (path === '/api/v1/agents/sessions') return reply({ data: [session()], has_more: false, last_id: null })
    if (path === '/api/agent') return reply({ agents: [agent] })
    if (path === '/api/agent/default-chatbot') return reply(agent)
    if (path === '/api/mention/plugins') return reply([])
    if (path === '/api/system/info') return reply({
      success: true, data: { branding: { name: 'Yuxi', logo: '/favicon.svg', title: '语析' } }
    })
    if (path === '/api/auth/me') return reply({ id: 1, uid: 'approval-test', username: '测试用户', role: 'user' })
    if (path === '/api/projects') return reply([])
    return reply({ data: [], databases: [], models: {}, first_run: false, enabled: false })
  })
  await page.addInitScript(() => localStorage.setItem('user_token', 'approval-browser-fixture'))
  const results = [], pageErrors = []
  const onPageError = error => pageErrors.push(error.message)
  page.on('pageerror', onPageError)
  try {
    for (const next of ['old', 'running', 'new']) {
      phase = 'old'
      await page.setViewportSize({ width: next === 'new' ? 768 : 1440, height: 900 })
      await page.evaluate((theme) => localStorage.setItem('theme', theme), next === 'new' ? 'dark' : 'light')
      await page.goto(`${origin}/agent/approval-thread`)
      const dialog = page.locator('[role="dialog"]:visible')
      await dialog.getByText('是否继续旧任务？', { exact: true }).waitFor()
      await dialog.locator('textarea').fill('继续')
      const held = new Promise(resolve => { onRequestHeld = resolve })
      failNextTurn = true
      await dialog.getByRole('button', { name: '提交', exact: true }).click()
      await held
      if (next !== 'old') {
        phase = next
        await page.evaluate(async () => {
          const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
          await useSessionRuntimeStore().resumeActiveRunForThread('approval-thread')
        })
        if (next === 'running') await dialog.waitFor({ state: 'hidden' })
        else await dialog.getByText('请确认新的任务？', { exact: true }).waitFor()
      }
      releaseFailure()
      await page.waitForFunction(() => {
        return import('/src/modules/session/model/sessionRuntime.js').then(({ useSessionRuntimeStore }) =>
          !useSessionRuntimeStore().getThreadState('approval-thread').approvalSubmitting)
      })
      const actual = await page.evaluate(async () => {
        const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
        const state = useSessionRuntimeStore().getThreadState('approval-thread')
        return { run: state.activeRunId, waitingRun: state.pendingInterrupt?.interruptedRunId || null, streaming: state.isStreaming }
      })
      check(actual.run === (next === 'old' ? 'run-A' : 'run-B'), '失败覆盖了当前 Run')
      check(actual.waitingRun === (next === 'running' ? null : next === 'old' ? 'run-A' : 'run-B'), '失败恢复了旧等待点')
      check(actual.streaming === (next === 'running'), '失败关闭了新 Run 的运行反馈')
      if (next === 'running') {
        check(await dialog.count() === 0, '旧审批框重新出现')
        await page.locator('.generating-status').waitFor()
      } else {
        await dialog.getByText(next === 'old' ? '是否继续旧任务？' : '请确认新的任务？', { exact: true }).waitFor()
        const submit = dialog.getByRole('button', { name: '提交', exact: true })
        check(await dialog.getByRole('button', { name: '停止本轮', exact: true }).isVisible(), '停止操作的后果需通过按钮文案说明')
        if (next === 'old') {
          await dialog.getByRole('alert').waitFor()
          check((await dialog.locator('textarea').inputValue()) === '继续', '失败清空了原问题的回答')
          check(await submit.isEnabled(), '失败后无法重试当前审批')
        } else {
          check(await dialog.getByRole('alert').count() === 0, '旧请求错误显示在新审批内')
          check((await dialog.locator('textarea').inputValue()) === '', '新审批复用了旧问题的回答')
          check(await submit.isDisabled(), '新问题需要新的回答')
        }
      }
      check(await page.getByText('准备报告，执行前请确认。', { exact: true }).isVisible(), '审批变化时会话历史仍可见')
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '审批工作区超出视口')
      await page.screenshot({ path: `/tmp/yuxi-approval-failure-${next}.png`, fullPage: true, animations: 'disabled' })
      results.push({ scenario: next, ...actual })
    }
    check(!pageErrors.length, `页面异常：${pageErrors.join('; ')}`)
    return results
  } finally {
    releaseFailure?.()
    page.off('pageerror', onPageError)
    await page.unrouteAll({ behavior: 'wait' })
  }
}
