// 确定性 API replay，核对真实 Vite 调试面板的顺序、正文和类型胶囊。
// 执行：playwright-cli -s=debug-projection run-code --filename=frontend/test/browser/messageDebugProjection.js
// prettier-ignore
async (page) => {
  const threadId = '00000000-0000-4000-8000-000000000031'
  const agentId = '00000000-0000-4000-8000-000000000032'
  const thread = { id: threadId, object: 'agent.session', status: 'completed', agent: { id: agentId }, created_at: 1791580000, metadata: {},
    yuxi: { title: '数据库调试投影验证', current_turn: null, context: {}, project_id: 'debug-project', config: {}, is_pinned: false } }
  const agent = { agent_id: agentId, name: '调试验证助手', config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const audits = [
    { id: 10, type: 'human', content: '数据库用户输入', run_id: 'run-1', input_id: 'input-1', created_at: '2026-10-10T04:22:54Z' },
    { id: 13, type: 'ai', operation_id: 'model-1', sequence: 3, content: '数据库中的第一模型调用', run_id: 'run-1', execution_status: 'completed', started_at: '2026-10-10T04:23:00Z', finished_at: '2026-10-10T04:23:02Z', duration_ms: 1900 },
    { id: 12, type: 'tool', operation_id: 'call-1', sequence: 6, content: '数据库中的工具输出', tool_name: 'read_file', tool_input: { path: '/example.txt' }, run_id: 'run-1', execution_status: 'completed', started_at: '2026-10-10T04:23:02Z', finished_at: '2026-10-10T04:23:03Z', duration_ms: 900 },
    { id: 11, type: 'ai', operation_id: 'model-final', sequence: 9, content: '数据库中的最终模型输出', run_id: 'run-1', execution_status: 'completed', started_at: '2026-10-10T04:23:03Z', finished_at: '2026-10-10T04:23:06Z', duration_ms: 2700, usage: { total_tokens: 1234 } },
    { id: 14, type: 'user', content: '未关联的持久输入', delivery_status: 'queued', input_id: 'input-2', created_at: '2026-10-10T04:24:00Z' }
  ]
  const runs = [{ run_id: 'run-1', status: 'completed', timing: { created_at: '2026-10-10T04:22:54Z', started_at: '2026-10-10T04:22:54Z', finished_at: '2026-10-10T04:23:06Z', total_latency_ms: 12000 } }]
  let mode = 'normal', release, delayed = true
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  await page.addInitScript(() => { localStorage.setItem('user_token', 'debug-replay-token'); localStorage.setItem('yuxi_debug_mode', 'true') })
  await page.route('**/api/**', async route => {
    const parsed = await page.evaluate(raw => { const url = new URL(raw); return { path: url.pathname, params: Object.fromEntries(url.searchParams) } }, route.request().url())
    const path = parsed.path
    let json
    if (path.endsWith('/audits')) {
      if (delayed) { delayed = false; await new Promise(resolve => { release = resolve }) }
      if (mode === 'error') return route.fulfill({ status: 503, json: { detail: '数据库不可用' } })
      const records = mode === 'failure' ? [...audits.slice(0, 4), { id: 15, type: 'ai', run_id: 'run-1', content: '', error_type: 'provider_error', error_message: '模型服务不可用' }, audits[4]] : audits
      json = { audits: mode === 'empty' ? [] : records, runs: mode === 'empty' ? [] : runs, truncated: false, runs_truncated: false }
    } else if (path === '/api/auth/me') json = { uid: '1', username: '调试验证', role: 'superadmin' }
    else if (path === '/api/agent') json = { agents: [agent] }
    else if (path === `/api/agent/${agentId}`) json = agent
    else if (path.endsWith('/items')) json = { data: [{ id: 'ordinary-final', object: 'agent.session.item', type: 'message', role: 'assistant', status: 'completed', turn_id: 'turn-1', content: [{ type: 'output_text', text: '聊天历史中的不同正文' }], yuxi: { run_id: 'run-1', message_id: 11 } }], has_more: false, yuxi: { runs: [] } }
    else if (path === '/api/v1/agents/sessions') json = { data: parsed.params.is_pinned === 'true' ? [] : [thread], has_more: false }
    else if (path.endsWith(`/${threadId}`) || path.endsWith('/viewed')) json = thread
    else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    else if (path.endsWith('/queue')) json = { inputs: [], status: 'ready' }
    else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    else if (path.endsWith('/attachments')) json = { files: [], attachments: [] }
    else if (path === '/api/projects') json = []
    else if (path.includes('/databases')) json = { databases: [] }
    else json = { data: [], files: [], skills: [], projects: [], providers: [], models: [], config: {}, site_name: '语析', first_run: false }
    await route.fulfill({ json })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`http://localhost:5173/agent/${threadId}`)
  await page.locator('.agent-debug-mode-btn').click()
  const panel = page.locator('.message-debug-panel')
  await panel.getByText('正在读取审计…', { exact: true }).waitFor()
  check(await panel.locator('.message-row').count() === 0, '加载时混入聊天历史')
  release()
  await panel.locator('.message-row').nth(4).waitFor()
  await page.getByRole('button', { name: '最大化面板', exact: true }).click()
  const summaries = await panel.locator('.message-row .record-summary').allTextContents()
  check(JSON.stringify(summaries) === JSON.stringify(['数据库用户输入', '数据库中的第一模型调用', 'read_file · 输出: 数据库中的工具输出', '数据库中的最终模型输出', '未关联的持久输入']), `顺序或正文偏离数据库: ${summaries}`)
  check(JSON.stringify(await panel.locator('.message-row .record-kind').allTextContents()) === JSON.stringify(['用户', '模型', '工具', '模型', '用户']), '类型胶囊不正确')
  check(await panel.getByText('聊天历史中的不同正文').count() === 0, '普通聊天正文覆盖数据库')
  await panel.locator('.message-row').nth(2).click()
  await panel.getByText('数据库中的工具输出', { exact: true }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-debug-projection-light.png' })
  const selectFilter = async (label) => {
    await panel.locator('.filter-select-field').click()
    await page.locator('.ant-select-dropdown:visible').getByText(label, { exact: true }).click()
  }
  await selectFilter('工具 1')
  check(await panel.locator('.message-row').count() === 1, '工具筛选不正确')
  await selectFilter('全部 5')
  await panel.getByRole('searchbox').fill('最终模型')
  check(await panel.locator('.message-row').count() === 1, '文本搜索不正确')
  await panel.getByRole('searchbox').fill('')
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.screenshot({ path: '/tmp/yuxi-debug-projection-dark.png' })
  await page.setViewportSize({ width: 375, height: 812 })
  await page.screenshot({ path: '/tmp/yuxi-debug-projection-mobile.png' })
  check(await panel.locator('.record-kind').first().isVisible(), '窄屏胶囊不可见')
  mode = 'error'
  await panel.getByRole('button', { name: '刷新审计', exact: true }).click()
  await panel.getByRole('alert').waitFor()
  check(await panel.locator('.message-row').count() === 5, '读取失败丢失已有数据库快照')
  mode = 'empty'
  await panel.getByRole('button', { name: '重试', exact: true }).click()
  await panel.getByText('当前会话暂无消息数据').waitFor()
  check(await panel.locator('.message-row').count() === 0, '空数据库混入聊天历史')
  mode = 'normal'
  await panel.getByRole('button', { name: '刷新审计', exact: true }).click()
  await panel.locator('.message-row').nth(4).waitFor()
  mode = 'failure'
  await panel.getByRole('button', { name: '刷新审计', exact: true }).click()
  await panel.locator('.kind-error').waitFor()
  await selectFilter('错误 1')
  check(await panel.locator('.message-row').count() === 1, '普通失败消息未进入错误筛选')
  await panel.locator('.message-row').click()
  await panel.getByRole('complementary', { name: '记录详情' }).getByText('模型服务不可用', { exact: true }).waitFor()
  return { summaries, capsules: true, loading: true, filters: true, details: true, narrowCapsuleVisible: true, errorRetention: true, empty: true, persistedFailure: true }
}
