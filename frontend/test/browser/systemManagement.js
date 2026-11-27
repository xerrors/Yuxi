// 在已打开应用、未登录的独立浏览器运行。Key 写入仅发生在 HTTP 夹具中。
async (page) => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const check = (value, message) => { if (!value) throw new Error(message) }
  const requests = []
  const member = { id: 101, uid: 'fixture-user', username: '示例用户', role: 'user', department_id: 1, department_name: '示例部门' }
  const agent = { agent_id: 'fixture-agent', name: '示例智能体', visibility: 'private', can_manage: true, can_run: true }
  const departments = Array.from({ length: 30 }, (_, index) => ({
    id: index + 1, name: `示例部门 ${index + 1}`, description: '组织管理布局示例', user_count: 1
  }))
  let role = 'superadmin'
  let key = null
  let holdOverview = false
  let staleOverview
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = request.url().slice(origin.length).split('?')[0]
    requests.push({ role, path })
    const respond = (json, status = 200) => route.fulfill({ json, status })
    if (request.method() === 'POST' && path === '/api/user/apikey/') {
      const payload = request.postDataJSON()
      check(payload.app_id === 'example-app' && payload.request_id, '创建请求丢失 APP 或幂等键')
      key = { ...payload, id: 1, key_prefix: 'fixture_', is_enabled: true }
      return respond({ api_key: key, secret: 'fixture-secret' })
    }
    if (request.method() !== 'GET') return respond({ detail: '夹具不支持此写入' }, 405)
    if (path === '/api/auth/me') return respond({ ...member, role, username: '验收账号' })
    if (path === '/api/user/apikey/') return respond({ api_keys: key && role === 'superadmin' ? [key] : [] })
    if (path === '/api/user/apikey/apps') {
      const apps = key ? [{ app_id: key.app_id, keys: [{ ...key, is_valid: true }], end_user_count: 0, input_count: 0 }] : []
      const snapshot = { apps, totals: { app_count: apps.length, key_count: apps.length, end_user_count: 0, input_count: 0 } }
      if (holdOverview) {
        holdOverview = false
        staleOverview = () => respond(snapshot)
        return
      }
      return respond(snapshot)
    }
    if (path === '/api/departments') return respond(departments)
    if (path === '/api/auth/users/page') return respond({ items: [member], total: 1 })
    if (path === '/api/system/discovery') return respond({ capabilities: { features: { knowledge: true } } })
    if (path === '/api/system/info') return respond({ success: true, data: { branding: { name: 'Yuxi', logo: '/favicon.svg' } } })
    if (path === '/api/auth/check-first-run') return respond({ first_run: false })
    if (path === '/api/agent') return respond({ agents: [agent] })
    if (path === '/api/agent/fixture-agent') return respond(agent)
    if (path === '/api/projects') return respond([])
    return respond({ data: [], items: [], agents: [], backends: [] })
  })

  const legacy = '/agent-manage?tab=integration&agent=fixture-agent#guide'
  const destination = '/system-manage?tab=integration&agent=fixture-agent#guide'
  await page.goto(origin + legacy)
  await page.waitForURL(origin + '/login')
  check(await page.evaluate(() => sessionStorage.getItem('redirect')) === destination, '访客登录返回链接丢失定位参数')

  await page.addInitScript(() => localStorage.setItem('user_token', 'system-management-fixture'))
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(origin + legacy)
  await page.waitForURL(origin + destination)
  const sidebar = page.locator('.nav').getByRole('link', { name: '系统管理', exact: true })
  const header = page.locator('.page-header')
  const createKey = page.getByRole('button', { name: '创建 API Key', exact: true })
  const overview = page.locator('.api-integration-panel')
  await overview.getByText('尚未创建绑定 APP 的 API Key').waitFor()
  await createKey.waitFor()
  const keyPanel = page.locator('.apikey-management')
  const overviewBox = await overview.boundingBox()
  check(overviewBox.y + overviewBox.height <= (await keyPanel.boundingBox()).y, 'API Key 列表应位于接入概览下方')
  check(await sidebar.getAttribute('aria-current') === 'page', '系统管理侧栏入口没有激活')
  check(await createKey.count() === 1, '同一页面重复展示创建入口')
  check(await header.getByRole('button', { name: 'API 集成' }).getAttribute('aria-pressed') === 'true', '旧链接没有激活 API 集成')
  holdOverview = true
  const refresh = overview.getByRole('button', { name: '刷新接入概览' })
  await Promise.all([page.waitForRequest(origin + '/api/user/apikey/apps'), refresh.click()])
  await createKey.click()
  const create = page.getByRole('dialog', { name: '创建 API Key' })
  await create.getByPlaceholder('如：生产环境API').fill('示例接入凭据')
  await create.getByText('APP 终端用户身份', { exact: true }).click()
  await create.getByPlaceholder('如：customer-service').fill('example-app')
  await create.getByRole('button', { name: /^创\s*建$/ }).click()
  const secret = page.getByRole('dialog').filter({ hasText: 'fixture-secret' })
  await secret.waitFor()
  await secret.getByRole('button', { name: 'Close' }).press('Enter')
  await secret.waitFor({ state: 'hidden' })
  await keyPanel.getByText('示例接入凭据', { exact: true }).waitFor()
  await overview.getByText('示例接入凭据 · agents', { exact: true }).waitFor()
  const staleResponse = page.waitForResponse(origin + '/api/user/apikey/apps')
  await staleOverview()
  await (await staleResponse).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  check(await overview.getByText('示例接入凭据 · agents', { exact: true }).isVisible(), '旧概览响应覆盖创建后的数据')
  await page.screenshot({ path: '/tmp/yuxi-system-management-api-light.png', animations: 'disabled' })

  await page.goto(origin + '/agent-manage?agent=fixture-agent')
  await page.waitForURL(origin + '/agent-manage?agent=fixture-agent')
  const agentEditor = page.getByRole('dialog', { name: '编辑智能体', exact: true })
  await agentEditor.waitFor()
  check(await agentEditor.locator('input').evaluateAll(
    (inputs) => inputs.some(input => input.value === '示例智能体')
  ), '旧智能体链接未定位到指定配置')
  await page.goto(origin + '/system-manage')
  await sidebar.waitFor()
  const agentEntry = page.locator('.nav').getByRole('link', { name: '智能体', exact: true })
  await agentEntry.click()
  await page.waitForURL(origin + '/agent-manage')
  await header.getByRole('heading', { name: '智能体管理' }).waitFor()
  await header.getByRole('button', { name: '定时任务' }).click()
  await page.locator('.scheduled-view').waitFor()
  check(await header.getByRole('button', { name: '模型供应商' }).count() === 0, '模型供应商仍在智能体页')
  await sidebar.click()
  await page.waitForURL(origin + '/system-manage')
  await page.locator('.model-provider-manage-panel').waitFor()
  await page.goto(origin + '/agent-manage?tab=providers#fixture')
  await page.waitForURL(origin + '/system-manage?tab=providers#fixture')
  await page.locator('.model-provider-manage-panel').waitFor()
  const systemTabs = await header.locator('nav').getByRole('button').allTextContents()
  check(systemTabs.map(label => label.trim()).join('|') === '模型供应商|API 集成|用户管理|部门管理|系统概览|智能体反馈', '系统管理顶部标签与迁移范围不符')

  const userTab = header.getByRole('button', { name: '用户管理', exact: true })
  const departmentTab = header.getByRole('button', { name: '部门管理', exact: true })
  await userTab.click()
  await page.getByText('示例用户', { exact: true }).waitFor()
  const search = page.getByPlaceholder('搜索用户名 / ID / 手机号')
  await search.fill('示例用户')
  await departmentTab.press('Enter')
  const lastDepartment = page.locator('.department-management').getByText('示例部门 30', { exact: true })
  await lastDepartment.waitFor()
  await userTab.click()
  check(await search.inputValue() === '示例用户', '切换管理标签丢失用户筛选条件')
  await page.goto(origin + '/system-manage?tab=organization&section=departments#fixture')
  await page.waitForURL(origin + '/system-manage?tab=departments#fixture')
  await lastDepartment.waitFor()
  await page.evaluate(() => localStorage.setItem('theme', 'dark'))
  await page.reload()
  await page.waitForFunction(() => document.documentElement.classList.contains('dark'))
  await lastDepartment.waitFor()
  for (const width of [1024, 768]) {
    await page.setViewportSize({ width, height: 900 })
    check(await header.evaluate((element) => element.scrollWidth <= element.clientWidth + 1), '管理标签在窄屏溢出')
    check(await page.locator('.settings-tab-panel').evaluate((element) => {
      element.scrollTop = element.scrollHeight
      const scrolled = element.scrollTop > 0 && element.scrollWidth <= element.clientWidth + 1
      element.scrollTop = 0
      return scrolled
    }), '长部门列表应在管理面板内纵向滚动')
  }
  await page.screenshot({ path: '/tmp/yuxi-system-management-organization-dark.png', animations: 'disabled' })

  await page.goto(origin + '/dashboard?tab=feedback#fixture')
  await page.waitForURL(origin + '/system-manage?tab=feedback#fixture')
  await header.getByRole('button', { name: '智能体反馈', pressed: true }).waitFor()
  await header.getByRole('button', { name: '系统概览' }).click()
  await page.waitForURL(origin + '/system-manage?tab=overview#fixture')
  await page.locator('.stats-overview-container').waitFor()
  await page.setViewportSize({ width: 768, height: 900 })
  const dashboardPanel = page.locator('.system-manage-content > .tab-panel:visible')
  check(await dashboardPanel.evaluate((element) => {
    element.scrollTop = element.scrollHeight
    return element.scrollTop > 0
  }), '系统概览在窄屏无法滚动到全部统计卡片')
  await header.getByRole('button', { name: '智能体反馈', exact: true }).click()
  await page.waitForURL(origin + '/system-manage?tab=feedback#fixture')
  await page.locator('.feedback-list').waitFor()
  check(await dashboardPanel.evaluate((element) => element.scrollTop === 0), '智能体反馈继承了系统概览的滚动位置')
  await page.goto(origin + '/system-manage?tab=dashboard&dashboardTab=feedback#fixture')
  await page.waitForURL(origin + '/system-manage?tab=feedback#fixture')
  await header.getByRole('button', { name: '智能体反馈', pressed: true }).waitFor()
  check(await header.count() === 1, '系统管理出现了重复页头')
  check(await page.locator('.nav').getByRole('link', { name: '数据总览', exact: true }).count() === 0, '数据总览仍有独立全局入口')
  check(await page.locator('.dashboard-header-actions').count() === 0, '数据总览仍显示旧快捷按钮')
  await page.goto(origin + '/system-manage?tab=departments')

  role = 'admin'
  await page.reload()
  await page.locator('.user-management').getByText('示例用户', { exact: true }).waitFor()
  check(await departmentTab.count() === 0, '部门管理员看到部门管理标签')
  check(!requests.some((entry) => entry.role === 'admin' && entry.path === '/api/departments'), '直接访问部门链接加载了部门列表')

  check(await header.getByRole('button', { name: /系统概览|智能体反馈/ }).count() === 0, '部门管理员看到数据总览标签')
  await page.goto(origin + '/dashboard')
  await page.waitForURL(url => url.pathname === '/agent')
  check(!requests.some((entry) => entry.role === 'admin' && entry.path.startsWith('/api/dashboard/')), '部门管理员直接访问旧链接加载了数据总览')

  role = 'user'
  await page.goto(origin + '/system-manage?tab=departments')
  await header.getByRole('button', { name: 'API 集成', pressed: true }).waitFor()
  check(await userTab.count() === 0 && await departmentTab.count() === 0, '普通用户看到用户或部门管理标签')
  check(await header.getByRole('button', { name: '模型供应商' }).count() === 0, '普通用户看到模型供应商标签')
  await createKey.waitFor()
  await page.getByText('暂无 API Key，点击上方按钮创建一个', { exact: true }).waitFor()
  check(await overview.count() === 0, '普通用户看到超管接入概览')
  check(!requests.some((entry) => entry.role === 'user' && [
    '/api/user/apikey/apps', '/api/departments', '/api/auth/users/page'
  ].includes(entry.path)), '普通用户访问了管理数据端点')
  return { roles: ['guest', 'superadmin', 'admin', 'user'], legacy: destination, widths: [1440, 1024, 768] }
}
