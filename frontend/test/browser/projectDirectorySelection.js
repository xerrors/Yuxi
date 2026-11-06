// 在真实 Vite 页面使用固定 API fixture 验证目录选择、失败恢复和项目交互；不替代真实后端验证。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/projectDirectorySelection.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = await page.evaluate(() => location.origin)
  const agent = { agent_id: 'picker-agent', name: '智能助手', can_run: true, is_default: true, config_json: { context: {} }, configurable_items: {}, metadata: {} }
  const tree = {
    '/': ['projects', 'notes', '.hidden'],
    '/projects': ['existing', 'research'],
    '/projects/existing': ['child', ...Array.from({ length: 24 }, (_, index) => `folder-${index}`)],
    '/projects/existing/child': [],
    '/projects/research': [],
    '/notes': [],
    '/.hidden': []
  }
  const projects = []
  const projectRequests = []
  let projectTreeRequests = 0
  let projectFailuresRemaining = 3
  let delayDirectory = false
  let delayedStarted = false
  let delayedDone = false
  let releaseDelayed
  await page.route('**/api/**', async route => {
    const [address, query = ''] = route.request().url().split('?')
    const path = address.replace(origin, '')
    const params = Object.fromEntries(query.split('&').filter(Boolean).map(part => part.split('=').map(decodeURIComponent)))
    const method = route.request().method()
    let json = { data: [], files: [], skills: [], projects: [], providers: [], config: {} }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/system/health')) json = { status: 'ok' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path.endsWith('/auth/me')) json = { id: 1, uid: 'picker-fixture', username: '界面测试', role: 'superadmin' }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === '/api/agent/picker-agent') json = agent
    if (path === '/api/v1/agents/sessions') json = { data: [], has_more: false }
    if (path === '/api/projects/history-candidates') json = { items: [{ thread_id: 'missing', title: '目录已删除的对话', workdir_path: 'projects/missing' }] }
    if (path === '/api/projects' && method === 'GET') json = projects
    if (path === '/api/projects' && method === 'POST') {
      const body = route.request().postDataJSON()
      projectRequests.push(body)
      if (projectFailuresRemaining > 0) {
        projectFailuresRemaining--
        return route.fulfill({ status: 409, json: { detail: '项目创建失败，请重试' } })
      }
      json = { id: projects.length ? `new-project-${projects.length}` : 'new-project', name: body.name, status: 'active', selection_status: 'selectable', directory_mode: 'linked' }
      projects.push(json)
    }
    if (path === '/api/projects/new-project' && method === 'PUT') {
      projects[0] = { ...projects[0], name: route.request().postDataJSON().name }
      json = projects[0]
    }
    if (path === '/api/workspace/tree') {
      const dir = params.path || '/'
      if (delayDirectory && dir === '/projects/existing') {
        delayDirectory = false
        delayedStarted = true
        await new Promise(resolve => { releaseDelayed = resolve })
        delayedDone = true
      }
      json = { entries: (tree[dir] || []).map(name => ({ name, is_dir: true, path: `${dir === '/' ? '' : dir}/${name}/` })) }
      if (params.include_unbound_project_dirs === 'true') projectTreeRequests++
    }
    if (path === '/api/workspace/directory' && method === 'POST') {
      const body = route.request().postDataJSON()
      if (body.name === '重复目录') return route.fulfill({ status: 409, json: { detail: '目录已存在' } })
      const dir = `${body.parent_path === '/' ? '' : body.parent_path}/${body.name}`
      tree[body.parent_path].push(body.name)
      tree[dir] = []
      json = { entry: { name: body.name, is_dir: true, path: `${dir}/` } }
    }
    return route.fulfill({ json })
  })
  await page.goto(origin)
  await page.evaluate(() => { localStorage.setItem('user_token', 'project-picker-fixture'); localStorage.setItem('chat-ui-store', JSON.stringify({ sidebarCollapsed: false })) })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${origin}/agent`)
  await page.locator('.chat-greeting-input').waitFor()
  const projectSelector = page.getByRole('region', { name: '新对话项目' })
  await projectSelector.getByRole('button', { name: '选择项目', exact: true }).click()
  await page.getByRole('button', { name: '新建项目', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '选择项目目录' })
  const open = dialog.locator('.directory-open')
  const row = name => dialog.getByRole('button', { name, exact: true })
  const idle = () => dialog.locator('.directory-content[aria-busy="false"]').waitFor()
  await row('projects').waitFor()
  check(projectTreeRequests > 0, '项目目录树必须包含未绑定目录')
  check(await open.isDisabled(), '未选目录时打开必须禁用')
  check(await row('.hidden').count() === 0, '默认隐藏以点开头的目录')
  await row('显示隐藏文件').click()
  await row('.hidden').waitFor()
  await row('隐藏隐藏文件').click()
  await row('projects').click()
  await row('existing').waitFor()
  check(await dialog.locator('.directory-column').count() === 2, '选择目录后应显示两列')
  check(await open.isEnabled(), '选中已有目录后可以打开')
  await page.screenshot({ path: '/tmp/yuxi-project-directory-light.png' })
  for (const width of [1024, 768]) {
    await page.setViewportSize({ width, height: 800 })
    const box = await dialog.boundingBox()
    check(box.x >= 0 && box.x + box.width <= width && box.y >= 0 && box.y + box.height <= 800, `${width}px 弹窗超出视口`)
  }
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('dark'); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
  await page.locator('.directory-open').evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)))
  await page.screenshot({ path: '/tmp/yuxi-project-directory-dark-768.png' })
  await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme('light'); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))) })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await row('编辑目录路径').click()
  const input = dialog.getByRole('textbox', { name: '编辑目录路径' })
  await input.fill('/pro')
  await idle()
  await row('projects').waitFor()
  check(await row('notes').count() === 0 && await open.isDisabled(), '部分路径只显示匹配项且不能打开')
  await input.fill('/missing')
  await idle()
  await row('projects').waitFor()
  await row('notes').waitFor()
  check(await open.isDisabled() && await row('新建文件夹').isDisabled(), '未匹配时回到主目录且操作禁用')
  await page.screenshot({ path: '/tmp/yuxi-project-directory-unmatched.png' })
  delayDirectory = true
  await input.fill('/projects/existing/')
  for (let i = 0; i < 100 && !delayedStarted; i++) await page.waitForTimeout(20)
  check(delayedStarted, '未触发延迟目录响应')
  await input.fill('/missing')
  await idle()
  await row('notes').waitFor()
  releaseDelayed()
  for (let i = 0; i < 100 && !delayedDone; i++) await page.waitForTimeout(20)
  check(delayedDone, '延迟请求未完成')
  await idle()
  check(await row('notes').count() === 1 && await row('child').count() === 0 && await open.isDisabled(), '过期路径结果覆盖了最新输入')
  await input.fill('/projects/existing/')
  await idle()
  await row('child').waitFor()
  check(await open.isDisabled(), '编辑路径时不能直接打开')
  await input.press('Enter')
  await idle()
  check(await open.isEnabled() && await dialog.locator('.directory-column').count() === 2, '精确路径确认后应选中目录')
  await row('child').click()
  await idle()
  check(await dialog.locator('.directory-column').count() === 2, '深层目录不能增加第三列')
  await dialog.getByRole('button', { name: 'existing', exact: true }).first().click()
  await idle()
  await row('新建文件夹').click()
  const folderDialog = page.getByRole('dialog', { name: '新建文件夹' })
  const folderInput = folderDialog.getByRole('textbox', { name: '文件夹名称' })
  await folderInput.fill('重复目录')
  await folderDialog.getByRole('button', { name: '创 建' }).click()
  await folderDialog.getByRole('alert').waitFor()
  check(await folderDialog.getByRole('alert').innerText() === '目录已存在', '应展示文件夹创建错误')
  check(await folderInput.inputValue() === '重复目录' && projectRequests.length === 0, '创建失败时应保留输入且不创建项目')
  await folderInput.fill('新建目录')
  await page.screenshot({ path: '/tmp/yuxi-project-directory-create-folder.png' })
  await folderDialog.getByRole('button', { name: '创 建' }).click()
  await folderDialog.waitFor({ state: 'hidden' })
  await idle()
  check(await dialog.locator('.directory-row').filter({ hasText: '新建目录' }).getAttribute('aria-pressed') === 'true', '新目录应自动选中')
  check(await dialog.locator('.directory-row').filter({ hasText: '新建目录' }).evaluate(el => { const row = el.getBoundingClientRect(); const column = el.parentElement.getBoundingClientRect(); return row.top >= column.top && row.bottom <= column.bottom }), '新目录应滚动到可见位置')
  check(await open.isEnabled(), '新目录选中后应能打开')
  const attemptProject = async () => {
    const response = page.waitForResponse(result => result.url().endsWith('/api/projects') && result.request().method() === 'POST')
    await open.click()
    await response
    await page.waitForFunction(() => !document.querySelector('.directory-open')?.disabled)
  }
  await attemptProject()
  await page.getByText('项目创建失败，请重试', { exact: true }).waitFor()
  check(await dialog.isVisible() && await open.isEnabled(), '项目创建失败后应保留目录以重试')
  await attemptProject()
  check(projectRequests[0].request_id === projectRequests[1].request_id, '相同目录重试必须保留请求 ID')
  await dialog.getByRole('navigation', { name: '目录路径' }).getByRole('button', { name: 'existing' }).click()
  await idle()
  await row('child').click()
  await idle()
  await attemptProject()
  check(projectRequests[2].request_id !== projectRequests[1].request_id && projectRequests[2].workdir.path === 'projects/existing/child', '改变目录后必须使用新的创建意图')
  await dialog.getByRole('navigation', { name: '目录路径' }).getByRole('button', { name: 'existing' }).click()
  await idle()
  await row('新建目录').click()
  await idle()
  await open.click()
  await dialog.waitFor({ state: 'hidden' })
  check(projectRequests.length === 4 && projectRequests[3].request_id === projectRequests[0].request_id && projectRequests[3].request_id !== projectRequests[2].request_id, '返回先前目录时必须复用该目录的请求 ID')
  check([0, 1, 3].every(index => projectRequests[index].name === '新建目录' && projectRequests[index].workdir.mode === 'linked' && projectRequests[index].workdir.path === 'projects/existing/新建目录'), '项目名称与绑定目录不一致')
  await projectSelector.getByRole('button', { name: '新建目录', exact: true }).waitFor()
  const projectRow = page.locator('.project-row').filter({ hasText: '新建目录' })
  await projectRow.waitFor()
  await projectRow.hover()
  await projectRow.getByRole('button', { name: '项目操作' }).click()
  await page.getByRole('menuitem', { name: '重命名项目' }).click()
  const renameDialog = page.locator('.ant-modal-confirm')
  await renameDialog.getByRole('textbox', { name: '项目名称' }).fill('重命名后的项目')
  await renameDialog.getByRole('button', { name: /保\s*存/ }).click()
  await projectSelector.getByRole('button', { name: '重命名后的项目', exact: true }).waitFor()
  check(projects[0].name === '重命名后的项目', '重命名后项目事实未更新')
  await projectSelector.getByRole('button', { name: '重命名后的项目', exact: true }).click()
  await page.getByRole('button', { name: '从历史对话添加' }).click()
  await page.getByRole('button', { name: '目录已删除的对话' }).click()
  await dialog.waitFor()
  await idle()
  await row('notes').waitFor()
  check(await open.isDisabled(), '历史对话目录不存在时不能创建项目')
  await row('取 消').click()
  await dialog.waitFor({ state: 'hidden' })
  await projectSelector.getByRole('button', { name: '重命名后的项目', exact: true }).click()
  await page.getByRole('button', { name: '新建项目', exact: true }).click()
  await row('notes').click()
  await idle()
  await row('新建文件夹').click()
  const longName = 'a'.repeat(140)
  await folderInput.fill(longName)
  await folderDialog.getByRole('button', { name: '创 建' }).click()
  await folderDialog.waitFor({ state: 'hidden' })
  await idle()
  await open.click()
  await dialog.waitFor({ state: 'hidden' })
  check(projectRequests[4].name === longName && projectRequests[4].workdir.path === `notes/${longName}`, '长目录名必须完整用于项目名称')
  await projectSelector.getByRole('button', { name: longName, exact: true }).waitFor()
  return { navigation: true, hiddenFolders: true, pathMatchingAndRace: true, folderFailureAndSelection: true, projectCreationRetryAndRename: true, longFolderName: true, missingHistoryDirectory: true, screenshots: ['/tmp/yuxi-project-directory-light.png', '/tmp/yuxi-project-directory-dark-768.png', '/tmp/yuxi-project-directory-unmatched.png', '/tmp/yuxi-project-directory-create-folder.png'] }
}
