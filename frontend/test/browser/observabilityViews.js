// 专用浏览器会话：playwright-cli -s=yuxi-observability run-code --filename=frontend/test/browser/observabilityViews.js
// 固定 API fixture 验证真实页面与交互；数据库和权限由对应 HTTP integration 验证。
// prettier-ignore
async page => {
  await page.waitForLoadState('networkidle')
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = 'http://localhost:5173'
  const errors = []
  const onError = error => errors.push(error.message)
  page.on('pageerror', onError)
  let usage = 'partial', historyFailure = false, emptyHistory = false
  const records = Array.from({ length: 25 }, (_, i) => ({
    id: `history-${i}`, trigger: i % 2 ? 'manual' : 'scheduled', status: i % 3 ? 'completed' : 'failed',
    scheduled_for: `2026-10-${String(29 - i).padStart(2, '0')}T01:00:00Z`, thread_id: `fixture-thread-${i}`,
    session_available: i % 3 !== 0, error_message: i % 3 ? null : `历史失败原因 ${i}`
  }))
  const job = { id: 'history-fixture', name: '项目进展整理', enabled: false, prompt: '整理项目进展，仅分析并提醒。',
    project_id: 'fixture-project', agent_slug: 'chatbot', cron_expression: '0 9 * * *', timezone: 'Asia/Shanghai', tool_approval_mode: 'default', runs: records.slice(0, 3) }
  await page.route('**/api/**', async route => {
    check(route.request().method() === 'GET', `界面验证不得写入：${route.request().method()}`)
    const requestUrl = route.request().url(), path = requestUrl.split('?')[0]
    let json = {}
    if (path.endsWith('/auth/me')) json = { id: 1, uid: 'observability-fixture', username: '验收用户', role: 'superadmin', department_id: 1 }
    if (path.endsWith('/system/health')) json = { status: 'healthy' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/api/projects')) json = [{ id: 'fixture-project', name: '示例项目', status: 'active', selection_status: 'selectable' }]
    if (path.endsWith('/api/agent')) json = { agents: [{ agent_id: 'chatbot', name: '智能助手', can_run: true }] }
    if (path.endsWith('/api/v1/agents/sessions')) json = { data: [], has_more: false }
    if (path.endsWith('/system/agent-feedback')) json = { items: [], has_more: false }
    if (path.endsWith('/api/scheduled-tasks')) json = { jobs: [job] }
    if (path.endsWith('/scheduled-tasks/history-fixture/runs')) {
      if (historyFailure) return route.fulfill({ status: 502, json: { detail: '历史暂不可用' } })
      const offset = Number(requestUrl.match(/[?&]offset=(\d+)/)?.[1] || 0)
      json = { items: emptyHistory ? [] : records.slice(offset, offset + 10), total: emptyHistory ? 0 : 25, limit: 10, offset }
    }
    if (path.endsWith('/dashboard/stats')) json = { total_sessions: 3, active_sessions: 1, total_messages: 6, total_users: 2 }
    if (path.endsWith('/stats/users')) json = { total_users: 2, active_users_24h: 1, active_users_30d: 1, daily_active_users: [] }
    if (path.endsWith('/stats/tools')) json = { total_calls: 0, success_rate: 0, failed_calls: 0, most_used_tools: [], tool_error_distribution: {} }
    if (path.endsWith('/stats/knowledge')) json = { total_knowledge_bases: 0, total_files: 0, total_storage_size: 0, file_type_distribution: {} }
    if (path.endsWith('/stats/agents/sessions')) json = { total_threads: 0, agent_count: 0, avg_messages_per_thread: 0, agent_distribution: [] }
    if (path.endsWith('/stats/vms')) json = { active_vms: 0, total_vms: 0 }
    if (path.endsWith('/stats/calls/timeseries')) {
      const unknown = usage === 'unknown', zero = usage === 'zero'
      json = { categories: ['input_tokens', 'output_tokens'], total_count: unknown ? null : zero ? 0 : 200,
        average_count: unknown ? null : zero ? 0 : 14.29, peak_count: unknown ? null : zero ? 0 : 120,
        token_usage_complete: zero, run_count: 5, incomplete_run_count: zero ? 0 : unknown ? 5 : 2,
        data: Array.from({ length: 14 }, (_, i) => ({ date: `2026-10-${String(16 + i).padStart(2, '0')}`,
          data: { input_tokens: unknown ? null : zero ? 0 : i === 0 ? 100 : i === 13 ? 50 : i === 2 ? null : 0,
                  output_tokens: unknown ? null : zero ? 0 : i === 0 ? 20 : i === 13 ? 30 : i === 2 ? null : 0 },
          total: unknown ? null : zero ? 0 : i === 0 ? 120 : i === 13 ? 80 : i === 2 ? null : 0,
          token_usage_complete: zero || (i !== 2 && i !== 13) })) }
    }
    return route.fulfill({ json })
  })
  try {
    await page.evaluate(async () => {
      const { useUserStore } = await import('/src/modules/identity/model/user.js')
      useUserStore().applySession({ access_token: 'observability-ui-fixture', user_id: 1, username: '验收用户', uid: 'observability-fixture', role: 'superadmin', department_id: 1 })
    })
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${origin}/dashboard`)
    const chart = page.locator('.call-stats')
    await chart.getByRole('button', { name: 'Token', exact: true }).click()
    await chart.getByText('部分缺报（2/5 次运行）', { exact: true }).waitFor()
    check((await chart.locator('.dashboard-metric-value').allTextContents()).join() === '200,14.29,120', '已报汇总错误')
    const series = await page.evaluate(async () => {
      const source = await (await fetch('/src/modules/dashboard/model/dashboardCharts.js')).text()
      const core = source.match(/from "([^"\n]*echarts_core[^"\n]*)"/)[1]
      const { getInstanceByDom } = await import(core)
      return getInstanceByDom(document.querySelector('.call-chart')).getOption().series.map(item => item.data)
    })
    check(series.every(values => values[2] === null), '图表把缺报转换成零')
    await chart.screenshot({ path: '/tmp/yuxi-observability-validation/usage-partial-light.png' })
    await page.evaluate(() => document.documentElement.classList.add('dark'))
    await chart.screenshot({ path: '/tmp/yuxi-observability-validation/usage-partial-dark.png' })
    await page.evaluate(() => document.documentElement.classList.remove('dark'))
    usage = 'unknown'
    await chart.getByRole('button', { name: '刷新调用统计', exact: true }).click()
    await chart.getByText('用量未上报（5 次运行）', { exact: true }).waitFor()
    check((await chart.locator('.dashboard-metric-value').allTextContents()).join() === '未知,未知,未知', '全缺报显示为零')
    await chart.screenshot({ path: '/tmp/yuxi-observability-validation/usage-unknown.png' })
    usage = 'zero'
    await chart.getByRole('button', { name: '刷新调用统计', exact: true }).click()
    await chart.getByText('上报完整', { exact: true }).waitFor()
    check((await chart.locator('.dashboard-metric-value').allTextContents()).join() === '0,0,0', '真实零被显示成未知')
    await page.goto(`${origin}/schedules`)
    await page.locator('.task-row').filter({ hasText: job.name }).click()
    const history = page.locator('.history-section')
    await history.getByText('25 条', { exact: true }).waitFor()
    check(await history.locator('.run-row').count() === 10, '第一页未显示十条')
    check(await history.locator('.run-row').first().isDisabled(), '无会话记录不应能跳转')
    await history.getByRole('button', { name: '下一页', exact: true }).click()
    await history.getByText('第 2 / 3 页', { exact: true }).waitFor()
    check(await history.getByText('历史失败原因 12', { exact: true }).isVisible(), '第二页缺少错误详情')
    await history.screenshot({ path: '/tmp/yuxi-observability-validation/history-page2-light.png' })
    await history.getByRole('button', { name: '下一页', exact: true }).click()
    await history.getByText('第 3 / 3 页', { exact: true }).waitFor()
    check(await history.locator('.run-row').count() === 5, '最后一页记录数错误')
    check(await history.getByRole('button', { name: '下一页', exact: true }).isDisabled(), '最后一页仍可下一页')
    await page.evaluate(() => document.documentElement.classList.add('dark'))
    await history.screenshot({ path: '/tmp/yuxi-observability-validation/history-dark.png' })
    await page.evaluate(() => document.documentElement.classList.remove('dark'))
    historyFailure = true
    await history.getByRole('button', { name: '刷新运行历史', exact: true }).click()
    await history.getByRole('alert').waitFor()
    check(await history.locator('.run-row').count() === 0, '请求失败仍展示旧记录')
    historyFailure = false
    await history.getByRole('button', { name: '重试', exact: true }).click()
    await history.getByText('第 1 / 3 页', { exact: true }).waitFor()
    for (const width of [1920, 1024, 768]) {
      await page.setViewportSize({ width, height: 1000 })
      await history.scrollIntoViewIfNeeded()
      check(await history.evaluate(el => el.scrollWidth <= el.clientWidth), `历史在 ${width}px 溢出`)
      await history.screenshot({ path: `/tmp/yuxi-observability-validation/history-${width}.png` })
    }
    emptyHistory = true
    await history.getByRole('button', { name: '刷新运行历史', exact: true }).click()
    await history.getByText('任务运行后，记录会显示在这里。', { exact: true }).waitFor()
    check(await history.getByRole('navigation').count() === 0, '空历史仍显示分页')
    check(errors.length === 0, `浏览器错误：${errors}`)
    return { usage: ['partial', 'unknown', 'zero'], nullChartGap: true, history: [10, 10, 5], retry: true, empty: true, themes: 2, widths: [1920, 1440, 1024, 768], pageErrors: errors }
  } finally {
    page.off('pageerror', onError)
    await page.evaluate(() => localStorage.removeItem('user_token'))
    await page.unrouteAll({ behavior: 'wait' })
  }
}
