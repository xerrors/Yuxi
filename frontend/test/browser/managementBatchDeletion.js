// 在独立浏览器打开应用后运行。所有 DELETE 仅由 HTTP 夹具处理。
async page => {
  const origin = page.url().startsWith('http') ? page.url().split('/').slice(0, 3).join('/') : 'http://127.0.0.1:5173'
  const check = (condition, reason) => { if (!condition) throw new Error(reason) }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    localStorage.setItem('user_token', 'batch-delete-fixture')
    localStorage.setItem('theme', 'light')
  })
  let role = 'superadmin'
  let refuseDepartment = true
  let failDepartmentsRead = false
  let releaseUser
  let releaseFilter
  let notifyFilterHeld
  const filterHeld = new Promise(resolve => { notifyFilterHeld = resolve })
  let releaseOldPage
  let notifyOldPageHeld
  const oldPageHeld = new Promise(resolve => { notifyOldPageHeld = resolve })
  let holdOldPage = false
  const sent = []
  let departments = [
    { id: 1, name: '默认部门', user_count: 1 },
    { id: 2, name: '设计部门', user_count: 1 },
    { id: 3, name: '研发部门', user_count: 1 }
  ]
  let users = [
    { id: 101, uid: 'fixture-self', username: '当前用户', role: 'superadmin' },
    { id: 102, uid: 'fixture-admin', username: '其他管理员', role: 'admin' },
    { id: 201, uid: 'fixture-member-a', username: '示例成员甲', role: 'user' },
    { id: 202, uid: 'fixture-member-b', username: '示例成员乙', role: 'user' },
    { id: 203, uid: 'fixture-member-c', username: '换号测试甲', role: 'user' },
    { id: 204, uid: 'fixture-member-d', username: '换号测试乙', role: 'user' },
    ...Array.from({ length: 14 }, (_, index) => ({
      id: 210 + index, uid: `fixture-page-${index}`, username: `分页示例 ${index + 1}`, role: 'user'
    })),
    { id: 301, uid: 'fixture-page-two-a', username: '第二页成员甲', role: 'user' },
    { id: 302, uid: 'fixture-page-two-b', username: '第二页成员乙', role: 'user' },
    { id: 303, uid: 'fixture-page-two-c', username: '第二页成员丙', role: 'user' }
  ]
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = request.url().slice(origin.length).split('?')[0]
    if (request.method() === 'DELETE') {
      sent.push(path)
      if (path === '/api/departments/3' && refuseDepartment) {
        return route.fulfill({ status: 409, json: { detail: { code: 'fixture_conflict', message: '示例冲突' } } })
      }
      if (path.startsWith('/api/departments/')) {
        departments = departments.filter(department => path !== `/api/departments/${department.id}`)
      } else if (path.startsWith('/api/auth/users/')) {
        if (path === '/api/auth/users/203') await new Promise(resolve => { releaseUser = resolve })
        users = users.filter(user => path !== `/api/auth/users/${user.id}`)
      } else {
        throw new Error('意外的删除地址：' + path)
      }
      return route.fulfill({ json: { success: true } })
    }
    if (request.method() !== 'GET') return route.fulfill({ status: 405, json: {} })
    if (path === '/api/departments' && failDepartmentsRead) return route.fulfill({ status: 503, json: {} })
    if (path === '/api/auth/users/page') {
      const offset = Number(request.url().match(/[?&]offset=(\d+)/)?.[1] ?? 0)
      const limit = Number(request.url().match(/[?&]limit=(\d+)/)?.[1] ?? 20)
      if (offset === 20 && holdOldPage) {
        await new Promise(resolve => {
          releaseOldPage = resolve
          notifyOldPageHeld()
        })
      }
      if (request.url().includes('search=unavailable')) {
        return route.fulfill({ status: 503, json: {} })
      }
      if (request.url().includes('search=fixture')) {
        await new Promise(resolve => {
          releaseFilter = resolve
          notifyFilterHeld()
        })
      }
      return route.fulfill({ json: { items: users.slice(offset, offset + limit), total: users.length } })
    }
    const fixtures = {
      '/api/auth/me': { ...users[0], role },
      '/api/departments': departments,
      '/api/system/info': { success: true, data: { branding: { name: 'Yuxi', logo: '/favicon.svg' } } },
      '/api/system/discovery': { capabilities: { features: { knowledge: false } } },
      '/api/projects': []
    }
    return route.fulfill({ json: fixtures[path] ?? { items: [], agents: [], data: [], backends: [] } })
  })
  const open = tab => page.goto(origin + '/system-manage?tab=' + tab, { waitUntil: 'domcontentloaded' })
  const batchButton = page.getByRole('button', { name: /批量删除/ })
  const confirmation = page.locator('.ant-modal-confirm:visible')
  const confirm = () => confirmation.getByRole('button', { name: /^删\s*除$/ }).press('Enter')
  const checkedDepartment = name => page.getByRole('checkbox', { name: '选择部门 ' + name, exact: true })
  const checkedUser = name => page.getByRole('checkbox', { name: '选择用户 ' + name, exact: true })
  const row = name => page.getByRole('row').filter({ hasText: name })
  const goToUserPage = number => page.locator(`.user-management .ant-pagination-item-${number}`).click()

  await open('departments')
  await row('默认部门').waitFor()
  check(await checkedDepartment('默认部门').isDisabled(), '默认部门仍可勾选')
  await page.locator('.department-management thead').getByRole('checkbox').check()
  check(await checkedDepartment('设计部门').isChecked() && await checkedDepartment('研发部门').isChecked(), '表头全选遗漏可删除部门')
  check(!(await checkedDepartment('默认部门').isChecked()), '表头全选包含默认部门')
  const departmentRefresh = page.locator('.department-management').getByTitle('刷新')
  failDepartmentsRead = true
  await departmentRefresh.click()
  await page.getByText('获取部门列表失败', { exact: true }).waitFor()
  check(await checkedDepartment('设计部门').isDisabled(), '部门读取失败后仍可勾选旧记录')
  check(await batchButton.isDisabled() && sent.length === 0, '部门读取失败后仍可批量删除')
  failDepartmentsRead = false
  await departmentRefresh.click()
  await page.getByText('获取部门列表失败', { exact: true }).waitFor({ state: 'hidden' })
  await batchButton.click()
  await confirmation.getByText(/迁移到默认部门/).waitFor()
  await page.waitForFunction(() =>
    document.activeElement?.closest('.ant-modal-confirm') &&
    document.activeElement.textContent.replace(/\s/g, '') === '取消'
  )
  await confirmation.getByRole('button', { name: /^取\s*消$/ }).press('Enter')
  await confirmation.waitFor({ state: 'hidden' })
  check(sent.length === 0, '取消确认仍提交删除')
  check(await checkedDepartment('研发部门').isChecked(), '取消确认丢失选择')

  await batchButton.press('Enter')
  await confirmation.getByText('确认批量删除部门', { exact: true }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-department-batch-delete.png', animations: 'disabled' })
  await confirm()
  await page.locator('.ant-message-warning').getByText('已删除 1 个部门，1 项结果未确认').waitFor()
  await confirmation.waitFor({ state: 'hidden' })
  await row('设计部门').waitFor({ state: 'hidden' })
  check(await checkedDepartment('研发部门').isChecked(), '部分失败后未保留仍在列表的失败项')
  check(sent.join('|') === '/api/departments/2|/api/departments/3', '批量删除部门的请求范围错误')

  refuseDepartment = false
  await batchButton.press('Enter')
  await confirm()
  await page.locator('.ant-message-success').getByText('已删除 1 个部门').waitFor()
  await row('研发部门').waitFor({ state: 'hidden' })

  await open('users')
  await row('其他管理员').waitFor()
  check(await checkedUser('当前用户').isDisabled(), '超管可勾选自己的账号')
  check(!(await checkedUser('其他管理员').isDisabled()), '超管无法勾选其他管理员')
  role = 'admin'
  await open('users')
  await row('当前用户').waitFor()
  check(await checkedUser('当前用户').isDisabled(), '当前账号仍可勾选')
  check(await checkedUser('其他管理员').isDisabled(), '部门管理员可勾选其他管理员')
  await checkedUser('示例成员甲').check()
  const search = page.getByPlaceholder('搜索用户名 / ID / 手机号')
  const oldPageResponse = page.waitForResponse(response => response.url().includes('/api/auth/users/page?') && response.url().includes('offset=20'))
  holdOldPage = true
  await goToUserPage(2)
  await oldPageHeld
  const appliedFilter = url => url.includes('/api/auth/users/page?') && url.includes('search=fixture')
  const filteredResponse = page.waitForResponse(response => appliedFilter(response.url()))
  await search.fill('fixture')
  await batchButton.waitFor({ state: 'hidden' })
  holdOldPage = false
  releaseOldPage()
  await (await oldPageResponse).finished()
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  check(await row('第二页成员甲').count() === 0, '旧分页响应覆盖了筛选范围')
  check(await checkedUser('示例成员甲').isDisabled(), '筛选等待期间仍可选择旧数据')
  await filterHeld
  releaseFilter()
  await filteredResponse
  await search.fill('unavailable')
  await page.getByText('获取用户列表失败', { exact: true }).waitFor()
  check(await checkedUser('示例成员甲').isDisabled(), '列表读取失败后仍可从旧数据勾选')
  const resetResponse = page.waitForResponse(response => response.url().includes('/api/auth/users/page?') && !response.url().includes('search='))
  await search.fill('')
  await resetResponse
  await checkedUser('示例成员甲').check()

  await goToUserPage(2)
  await row('第二页成员甲').waitFor()
  await page.getByRole('button', { name: '批量删除 (1)', exact: true }).waitFor()
  const userSelectAll = page.locator('.user-management thead').getByRole('checkbox')
  await userSelectAll.check()
  await page.getByRole('button', { name: '批量删除 (4)', exact: true }).waitFor()
  await userSelectAll.uncheck()
  await page.getByRole('button', { name: '批量删除 (1)', exact: true }).waitFor()
  await checkedUser('第二页成员乙').check()
  users = users.map(user => user.id === 302 ? { ...user, role: 'admin' } : user)
  await page.locator('.user-management .yuxi-refresh-button').click()
  await row('第二页成员乙').getByText('管理员', { exact: true }).waitFor()
  check(await checkedUser('第二页成员乙').isDisabled(), '重新读取后仍可勾选已升级的管理员')
  await page.getByRole('button', { name: '批量删除 (1)', exact: true }).waitFor()
  await checkedUser('第二页成员甲').check()
  await page.getByRole('button', { name: '清空选择' }).click()
  await batchButton.waitFor({ state: 'hidden' })
  await goToUserPage(1)
  await row('示例成员甲').waitFor()
  check(!(await checkedUser('示例成员甲').isChecked()), '清空选择遗漏其他页')
  await checkedUser('示例成员甲').check()
  await goToUserPage(2)
  await checkedUser('第二页成员甲').check()
  await page.getByRole('button', { name: '批量删除 (2)', exact: true }).waitFor()
  await page.waitForFunction(() => !document.querySelector('.ant-message-notice'))
  for (const width of [1440, 1024, 768]) {
    await page.setViewportSize({ width, height: 900 })
    const layout = await page.locator('.user-management').evaluate(panel => {
      const bounds = selector => panel.querySelector(selector).getBoundingClientRect()
      const header = bounds('.settings-header')
      const toolbar = bounds('.user-table-toolbar')
      const search = bounds('.search-input')
      const filter = bounds('.user-kind-select')
      const actions = bounds('.user-selection-actions')
      const table = bounds('.settings-table-wrapper')
      return {
        groupedFilters: Math.abs(search.top - filter.top) < 1 && search.right <= filter.left,
        actionsNearTable: header.bottom <= actions.top && actions.bottom <= table.top,
        actionsFit: toolbar.left <= actions.left && actions.right <= toolbar.right + 1,
        sharedRow: Math.abs(search.top - actions.top) < 1
      }
    })
    check(layout.groupedFilters && layout.actionsNearTable && layout.actionsFit, `${width}px 下用户工具栏未对齐或溢出`)
    if (width >= 1024) check(layout.sharedRow, `${width}px 下筛选与多选操作未对齐到同一行`)
    if (width !== 1024) {
      await page.screenshot({ path: `/tmp/yuxi-user-management-toolbar-${width}.png`, animations: 'disabled' })
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.screenshot({ path: '/tmp/yuxi-user-cross-page-selection.png', animations: 'disabled' })
  await goToUserPage(1)
  await row('示例成员甲').waitFor()
  check(await checkedUser('示例成员甲').isChecked(), '回到第一页时未恢复选中状态')
  await batchButton.press('Enter')
  await confirmation.getByText(/选中的 2 个用户/).waitFor()
  await confirm()
  await page.locator('.ant-message-success').getByText('已删除 2 个用户').waitFor()
  await confirmation.waitFor({ state: 'hidden' })
  await row('示例成员甲').waitFor({ state: 'hidden' })
  check(sent.slice(-2).join('|') === '/api/auth/users/201|/api/auth/users/301', '批量删除未覆盖所有选中页或误删其他用户')

  await goToUserPage(2)
  await checkedUser('第二页成员丙').check()
  await goToUserPage(1)
  await checkedUser('示例成员乙').check()
  await row('示例成员乙').locator('button.ant-btn-dangerous').press('Enter')
  await confirmation.getByText('确认删除用户', { exact: true }).waitFor()
  await confirm()
  await confirmation.waitFor({ state: 'hidden' })
  await row('示例成员乙').waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: '批量删除 (1)', exact: true }).waitFor()
  await page.getByRole('button', { name: '清空选择' }).click()

  await checkedUser('换号测试甲').check()
  await checkedUser('换号测试乙').check()
  await batchButton.press('Enter')
  const firstUserRequest = page.waitForRequest(request => request.method() === 'DELETE' && request.url().endsWith('/api/auth/users/203'))
  await confirm()
  await firstUserRequest
  const cancel = confirmation.getByRole('button', { name: /^取\s*消$/ })
  await page.waitForFunction(() =>
    [...document.querySelectorAll('.ant-modal-confirm button')].some(button => button.textContent.replace(/\s/g, '') === '取消' && button.disabled)
  )
  check(await cancel.isDisabled(), '已开始删除后仍可取消确认框')
  check(sent.at(-1) === '/api/auth/users/203', '首项完成前已请求删除下一项')
  await page.evaluate(() => {
    const app = document.querySelector('#app').__vue_app__
    app.config.globalProperties.$pinia._s.get('user').token = 'replacement-session-fixture'
  })
  releaseUser()
  await confirmation.waitFor({ state: 'hidden' })
  check(!sent.includes('/api/auth/users/204'), '使用替换后的凭据继续删除')
  await batchButton.waitFor({ state: 'hidden' })
  return { protectedRows: true, cancellation: true, partialFailure: true, crossPageBatch: true, sessionChangeStopsBatch: true }
}
