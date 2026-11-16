// 已登录开发环境：playwright-cli -s=<session> run-code --filename=frontend/test/browser/scheduledAgentEditor.js
// 仅拦截列表读取；不创建、修改或运行真实任务。
// prettier-ignore
async (page) => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const job = {
    id: 'schedule-layout-fixture', name: '天气预报', enabled: true,
    prompt: '对比苏州和北京未来七天的天气，整理温度、湿度和天气变化。',
    project_id: 'layout-project', agent_slug: 'default-chatbot', model_spec: 'deepseek:deepseek-chat',
    cron_expression: '0 9 * * *', timezone: 'Asia/Shanghai', tool_approval_mode: 'default',
    runs: [
      { id: 'run-one', trigger: 'scheduled', status: 'completed', scheduled_for: '2026-09-11T01:00:00Z', session_available: true, thread_id: 'fixture-thread' },
      { id: 'run-two', trigger: 'manual', status: 'interrupted', scheduled_for: '2026-09-10T03:12:00Z', session_available: true, thread_id: 'fixture-thread', error_message: '需要用户审批工具操作' }
    ]
  }
  let projectRequests = 0
  let releaseRefresh
  let delayRefresh = false
  await page.route('**/api/projects', async route => {
    check(route.request().method() === 'GET', '测试不得写入项目')
    projectRequests++
    if (delayRefresh) await new Promise(resolve => { releaseRefresh = resolve })
    await route.fulfill({ json: [{ id: 'layout-project', name: '天气', selection_status: 'selectable', status: 'active' }] })
  })
  await page.route('**/api/scheduled-tasks', route => {
    check(route.request().method() === 'GET', '测试不得写入任务')
    return route.fulfill({ json: { jobs: [job] } })
  })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('http://localhost:5173/schedules')
  await page.locator('.task-row').waitFor()
  const expand = page.getByRole('button', { name: '展开侧边栏', exact: true })
  if (await expand.isVisible()) await expand.click()
  await page.locator('.project-row').filter({ hasText: '天气' }).waitFor()
  const before = projectRequests
  await page.locator('.task-row').filter({ hasText: '天气预报' }).click()
  const name = page.getByRole('textbox', { name: '任务名称', exact: true })
  await name.waitFor()
  await page.waitForTimeout(300)
  check(await name.evaluate(el => document.activeElement === el && el.selectionEnd === el.value.length), '打开详情必须聚焦并选中名称')
  check(projectRequests === before, '打开详情不得重新加载已缓存项目')
  delayRefresh = true
  await page.locator('.setting-control').getByRole('button', { name: '天气', exact: true }).click()
  await page.locator('.project-loading').waitFor()
  check(await page.locator('.project-row').filter({ hasText: '天气' }).isVisible(), '刷新期间侧边栏项目必须保留')
  check(await page.getByText('正在加载项目...', { exact: true }).count() === 0, '侧边栏不得闪现刷新提示')
  releaseRefresh()
  await page.locator('.project-loading').waitFor({ state: 'hidden' })
  await page.locator('.action-dropdown-panel:visible').press('Escape')
  await page.locator('.action-dropdown-panel').waitFor({ state: 'hidden' })
  const row = page.locator('.run-row').first()
  const beforeHover = await row.boundingBox()
  await row.hover()
  const metrics = await row.evaluate(el => {
    const r = el.getBoundingClientRect(), text = el.firstElementChild.getBoundingClientRect()
    return { inset: text.left - r.left, background: getComputedStyle(el).backgroundColor }
  })
  check(metrics.inset >= 12, '历史记录悬浮背景必须有水平内边距')
  check((await row.boundingBox()).width === beforeHover.width, '悬浮不得改变行宽')
  const alignment = await page.locator('.frequency-content').evaluate(el => {
    const label = el.firstElementChild.getBoundingClientRect(), options = el.lastElementChild.getBoundingClientRect()
    return Math.abs(label.top + label.height / 2 - options.top - options.height / 2)
  })
  check(alignment < 1, '重复频率标签与选项必须垂直居中')
  await page.screenshot({ path: '/tmp/yuxi-schedule-light.png' })
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  await page.screenshot({ path: '/tmp/yuxi-schedule-dark.png' })
  await page.evaluate(() => document.documentElement.classList.remove('dark'))
  await page.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
  await page.setViewportSize({ width: 480, height: 844 })
  await page.waitForTimeout(200)
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '窄屏不得横向溢出')
  await page.screenshot({ path: '/tmp/yuxi-schedule-narrow.png' })
  await page.getByRole('button', { name: '关闭任务详情' }).click()
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await name.waitFor()
  check(await name.inputValue() === '新建定时任务', '新任务名称必须有可编辑默认值')
  check(await name.evaluate(el => document.activeElement === el && el.selectionStart === 0 && el.selectionEnd === el.value.length), '新建任务必须聚焦并选中默认名称')
  const agentTrigger = page.locator('.setting-control').first().getByRole('button')
  check(await page.locator('select[aria-label="执行智能体"]').count() === 0, '智能体选择不得使用原生 select')
  await agentTrigger.click()
  const agentSearch = page.getByRole('textbox', { name: '搜索智能体', exact: true })
  await agentSearch.fill('不存在的智能体-fixture')
  await page.getByRole('status').filter({ hasText: '没有匹配的智能体' }).waitFor()
  await agentSearch.fill('')
  const agentOption = page.getByRole('menu', { name: '执行智能体', exact: true }).getByRole('menuitemradio').last()
  const agentLabel = (await agentOption.innerText()).trim()
  await agentSearch.fill(agentLabel)
  await agentOption.click()
  await page.locator('.action-dropdown-panel:visible').waitFor({ state: 'hidden' })
  check((await agentTrigger.innerText()).trim() === agentLabel, '选择智能体后名称必须更新')
  await agentTrigger.click()
  check(await page.getByRole('menuitemradio', { name: agentLabel, exact: true }).getAttribute('aria-checked') === 'true', '当前智能体必须显示选中状态')
  await page.getByRole('textbox', { name: '搜索智能体', exact: true }).press('Escape')
  await page.locator('.action-dropdown-panel:visible').waitFor({ state: 'hidden' })
  check(await agentTrigger.evaluate(el => document.activeElement === el), 'Escape 关闭后焦点必须返回智能体按钮')
  await name.fill('每周摘要')
  check(await name.inputValue() === '每周摘要', '默认名称必须允许修改')
  await page.getByRole('button', { name: '关闭任务详情' }).click()
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  return { projectRequests, hoverInset: metrics.inset, frequencyCenterDelta: alignment, focus: 'passed', defaultName: 'passed', sidebar: 'passed', agentDropdown: 'passed' }
}
