// 在部署页面回放文件协议，不访问在线账户或文件。
// playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/sessionFileTrees.js
// prettier-ignore
async page => {
  await page.unrouteAll({ behavior: 'wait' })
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const threadId = 'file-trees-session'
  const memberId = 'file-trees-member'
  const agent = { agent_id: 'file-trees-agent', name: '智能助手', can_run: true, is_default: true, config_json: { context: {} }, configurable_items: {} }
  const project = { id: 'file-trees-project', name: '交付资料', status: 'active', directory_mode: 'linked', selection_status: 'selectable' }
  const thread = { id: threadId, object: 'agent.session', status: 'completed', agent: { id: agent.agent_id, model: 'fixture:chat' }, metadata: {}, created_at: 1795766400, last_active_at: 1795766400,
    yuxi: { title: '核对交付资料', project_id: project.id, context: {}, config: {}, current_turn: null } }
  const prompt = '请把项目交付材料和个人备忘分开整理。'
  const answer = '已将项目交付材料和验收记录放在项目文件夹。个人备忘和草稿仍放在个人空间。'
  const message = (role, type, text) => ({ id: `file-trees-${role}`, object: 'agent.session.item', type: 'message', role, status: 'completed', turn_id: 'file-trees-turn',
    content: [{ type, text }], yuxi: { message_id: `file-trees-${role}`, run_id: 'file-trees-run', created_at: '2026-11-27T08:00:00Z' } })
  const file = name => ({ name, path: `/${name}`, is_dir: false, size: 32, modified_at: '2026-11-27T08:00:00Z' })
  const projectEntry = file('交付与发布前的完整验收资料汇总和待核验证据清单.txt')
  const personalEntry = file('个人备忘.txt')
  const sharedEntry = file('同名资料.txt')
  const projectSearchEntry = file('项目搜索结果.txt')
  const personalSearchEntry = file('个人搜索结果.txt')
  const artifact = file('会话交付报告.txt')
  const memberArtifact = file('成员交付清单.txt')
  const artifactSourcePath = (entry) => `/home/user/workspace/outputs${entry.path}`
  const artifactPath = artifactSourcePath(artifact)
  const savedDirectory = { ...file('saved_artifacts'), is_dir: true }
  const presented = (entry) => ({
    id: 'file-trees-artifact-call', object: 'agent.session.item', type: 'function_call',
    status: 'completed', turn_id: 'file-trees-turn', call_id: 'present-report',
    name: 'present_artifacts', arguments: JSON.stringify({ filepaths: [artifactSourcePath(entry)] }),
    yuxi: { message_id: 'file-trees-assistant', run_id: 'file-trees-run' }
  })
  const projectFiles = [projectEntry, sharedEntry, ...Array.from({ length: 25 }, (_, i) => file(`项目草稿 ${i + 1}.txt`))]
  const personalFiles = [personalEntry, sharedEntry, ...Array.from({ length: 25 }, (_, i) => file(`个人记录 ${i + 1}.txt`))]
  const treeRequests = []
  const searches = []
  const previews = []
  const saves = []
  const savedArtifacts = []
  const firstProjectLoad = Promise.withResolvers()
  let waitingForFirstProjectLoad = true
  let allowPersonalLoad
  const firstPersonalLoad = new Promise(resolve => { allowPersonalLoad = resolve })
  let waitingForFirstPersonalLoad = true
  let userRole = 'user'
  let memberExists = false
  let projectEmpty = false
  let personalError = false
  let blockedPersonalRefresh = null
  let accountSwap = null
  const replacementToken = 'session-file-trees-second-account'
  const endpointParts = url => {
    const [path, query = ''] = url.replace(origin, '').split('?')
    const params = Object.fromEntries(query.split('&').filter(Boolean).map(part =>
      part.split('=').map(value => decodeURIComponent(value.replace(/\+/g, ' ')))))
    return { path, params }
  }
  await page.route('https://api.github.com/**', route => route.fulfill({ json: { stargazers_count: 0 } }))
  await page.route('**/api/**', async route => {
    const { path, params } = endpointParts(route.request().url())
    const method = route.request().method()
    const secondAccount = route.request().headers().authorization === `Bearer ${replacementToken}`
    const scopedFile = path.match(/^\/api\/(viewer\/filesystem|workspace)\/(tree|search|file|open-file)$/)
    if (scopedFile) {
      const projectScope = scopedFile[1] === 'viewer/filesystem'
      const scope = projectScope ? 'project' : 'personal'
      const operation = scopedFile[2]
      if (projectScope && params.thread_id !== threadId) return route.fulfill({ status: 400, json: { detail: '缺少当前对话' } })
      if (operation === 'tree') {
        if (!projectScope && params.path === savedDirectory.path) {
          return route.fulfill({ json: { entries: savedArtifacts, path: savedDirectory.path } })
        }
        treeRequests.push(scope)
        if (projectScope && waitingForFirstProjectLoad) {
          waitingForFirstProjectLoad = false
          await firstProjectLoad.promise
        }
        if (!projectScope && waitingForFirstPersonalLoad) {
          waitingForFirstPersonalLoad = false
          await firstPersonalLoad
        }
        if (!projectScope && accountSwap) {
          const request = secondAccount ? accountSwap.next : accountSwap.previous
          request.started.resolve()
          await request.finish.promise
          return route.fulfill({ json: { entries: [secondAccount ? accountSwap.nextFile : accountSwap.previousFile], path: '/' } })
        }
        if (!projectScope && blockedPersonalRefresh) {
          const refresh = blockedPersonalRefresh
          blockedPersonalRefresh = null
          refresh.started.resolve()
          await refresh.finish.promise
          return route.fulfill({ status: 503, json: { detail: 'fixture unavailable' } })
        }
        if (!projectScope && personalError) return route.fulfill({ status: 503, json: { detail: 'fixture unavailable' } })
        const entries = projectScope ? (projectEmpty ? [] : projectFiles) : personalFiles
        return route.fulfill({ json: { entries, path: '/' } })
      }
      if (operation === 'search') {
        searches.push([scope, params.query])
        return route.fulfill({ json: { entries: [projectScope ? projectSearchEntry : personalSearchEntry] } })
      }
      previews.push([scope, method, params.path])
      const body = `${projectScope ? '项目文件' : '个人文件'}正文 ${params.path}`
      return route.fulfill({ contentType: 'text/plain; charset=utf-8', body })
    }

    const memberPath = `/api/v1/agents/sessions/${memberId}`
    const isMember = path === memberPath || path.startsWith(`${memberPath}/`)
    const sourceId = isMember ? memberId : threadId
    const sourceArtifact = isMember ? memberArtifact : artifact
    const sessionPath = `/api/v1/agents/sessions/${sourceId}`
    if (path === `${sessionPath}/artifacts/save` && method === 'POST') {
      const request = route.request().postDataJSON()
      saves.push({ threadId: sourceId, ...request })
      const savedArtifact = { ...sourceArtifact, path: `${savedDirectory.path}${sourceArtifact.path}` }
      if (!savedArtifacts.length) personalFiles.push(savedDirectory)
      savedArtifacts.push(savedArtifact)
      return route.fulfill({ json: { saved_path: savedArtifact.path } })
    }
    if (decodeURI(path) === `${sessionPath}/artifacts${artifactPath}` && params.download === 'true') {
      return route.fulfill({
        body: '确定性交付文件',
        headers: { 'Content-Type': 'text/plain', 'Content-Disposition': 'attachment; filename="artifact.txt"' }
      })
    }
    let json = { data: [], entries: [], attachments: [], files: [], skills: [], config: {} }
    if (accountSwap && !secondAccount && path === `${sessionPath}/attachments`) {
      accountSwap.attachmentRefresh.started.resolve()
      await accountSwap.attachmentRefresh.finish.promise
    }
    if (path.endsWith('/system/info')) json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path.endsWith('/auth/me')) json = { id: 'file-trees-user', uid: 'file-trees-user', username: '侧栏验收', role: userRole }
    if (path === '/api/agent') json = { agents: [agent] }
    if (path === `/api/agent/${agent.agent_id}`) json = agent
    if (path === '/api/projects') json = [project]
    if (path === '/api/v1/agents/sessions') json = { data: [thread], has_more: false }
    if (path === sessionPath || path === `${sessionPath}/viewed`) json = isMember
      ? { ...thread, id: memberId, yuxi: { ...thread.yuxi, title: '资料整理', parent_session_id: threadId } }
      : thread
    if (path === `${sessionPath}/items`) json = {
      data: [message('user', 'input_text', prompt), presented(sourceArtifact), message('assistant', 'output_text', answer)],
      last_id: 'file-trees-assistant', has_more: false, yuxi: { runs: [] }
    }
    if (path === `${sessionPath}/state`) json = { agent_state: {}, interrupt: null }
    if (path === `${sessionPath}/queue`) json = { inputs: [], queue_paused: false }
    if (path === `${sessionPath}/cooperation`) {
      const sessions = [{ session_id: threadId, path: '/root', name: '核对交付资料' }]
      if (memberExists) sessions.push({ session_id: memberId, parent_session_id: threadId, path: '/root/资料整理', name: '资料整理' })
      json = { sessions, members: [], edges: [] }
    }
    if (path === `${sessionPath}/events`) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
    return route.fulfill({ json })
  })

  await page.addInitScript(() => {
    localStorage.setItem('user_token', 'session-file-trees-fixture')
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', 'light')
  })
  const panel = page.locator('.agent-panel')
  const tab = name => panel.getByRole('tab', { name, exact: true })
  const action = name => panel.getByRole('button', { name, exact: true })
  const start = tab('开始')
  const plus = action('新建开始页')
  const divider = panel.getByRole('separator', { name: '调整侧边栏宽度' })
  const projectTab = tab('工作区文件')
  const personalTab = tab('个人空间文件')
  const projectTree = panel.getByRole('region', { name: '工作区文件', exact: true })
  const personalTree = panel.getByRole('region', { name: '个人空间文件', exact: true })
  const preview = panel.locator('.preview-pane')
  const row = (tree, entry) => tree.getByTitle(entry.name, { exact: true })
  const isSelected = async (target) => await target.getAttribute('aria-selected') === 'true'
  const checkTabColors = async (selectedColor) => {
    const colors = await panel.evaluate(node => ({
      panel: getComputedStyle(node).backgroundColor,
      header: getComputedStyle(node.querySelector('.panel-header')).backgroundColor,
      selected: getComputedStyle(node.querySelector('[role="tab"][aria-selected="true"]').parentElement).backgroundColor
    }))
    check(colors.header === colors.panel && colors.selected === selectedColor, '标签栏背景与面板一致，选中标签使用当前主题灰色')
  }
  const waitForPanelMotion = () => panel.evaluate(node =>
    Promise.allSettled(node.closest('.side-panel').getAnimations().map(animation => animation.finished))
  )
  const checkPlusPosition = async () => {
    await waitForPanelMotion()
    const gap = await plus.evaluate(button => {
      const lastTab = button.closest('.panel-header').querySelector('[role="tablist"]').lastElementChild
      return button.getBoundingClientRect().left - lastTab.getBoundingClientRect().right
    })
    check(gap >= 0 && gap <= 12, `加号应紧跟最后一个标签，实际间距 ${gap}px`)
  }
  const checkStartPosition = async () => {
    const position = await panel.locator('.panel-start').evaluate(pane => {
      const { top, height } = pane.getBoundingClientRect()
      const actions = pane.querySelector('.panel-start-actions').getBoundingClientRect()
      return (actions.top + actions.height / 2 - top) / height
    })
    check(position >= 0.2 && position <= 0.4, `开始入口应在面板上部，实测中心比例 ${position}`)
  }
  const checkDividerHighlight = async (color) => {
    await page.waitForFunction(([node, expected]) => {
      const line = getComputedStyle(node, '::before')
      return line.width === '3px' && line.backgroundColor === expected
    }, [await divider.elementHandle(), color], { timeout: 2000 })
      .catch(() => { throw Error(`悬停和拖拽时分割线应使用灰色 ${color}`) })
  }
  const dragAt = async (heightRatio, deltaX, color) => {
    const box = await divider.boundingBox()
    const before = await panel.boundingBox()
    check(Math.abs(box.y - before.y) < 1 && Math.abs(box.height - before.height) < 1, '整条分割线应可拖拽')
    await page.mouse.move(box.x + box.width / 2, box.y + box.height * heightRatio)
    await checkDividerHighlight(color)
    await page.mouse.down()
    try {
      await page.mouse.move(box.x + box.width / 2 + deltaX, box.y + box.height * heightRatio, { steps: 8 })
      await page.waitForFunction(expected => {
        const sidebar = document.getElementById('agent-file-panel')
        return sidebar.classList.contains('no-transition') &&
          Math.abs(sidebar.getBoundingClientRect().width - expected) <= 1
      }, before.width - deltaX, { timeout: 2000 })
      await checkDividerHighlight(color)
    } finally {
      await page.mouse.up()
    }
    const after = await panel.boundingBox()
    check(Math.abs(after.width - (before.width - deltaX)) <= 1, '松开后保留拖拽宽度')
  }
  const openPanel = async () => {
    await page.getByText(answer, { exact: true }).waitFor()
    await page.getByRole('button', { name: '打开侧边栏', exact: true }).click()
    await start.waitFor()
    await waitForPanelMotion()
    check(await isSelected(start), '打开侧边栏时应默认选中开始页')
  }
  const choose = async (name) => {
    if (!await start.count()) await plus.click()
    check(await isSelected(start) && await plus.count() === 0, '应打开并聚焦唯一的开始页')
    await action(name).click()
    check(await isSelected(tab(name)), `${name} 应成为已选标签`)
    check(await tab(name).count() === 1, `${name} 应复用同一个标签`)
    check(await start.count() === 0 && await plus.isEnabled(), '选择工具后应关闭开始页并显示加号')
  }
  const showPreview = async (text) => {
    await preview.getByText(text, { exact: true }).waitFor()
    check(await preview.getByTitle('全屏预览', { exact: true }).count() === 0, '文件预览不应出现全屏按钮')
    await preview.getByTitle('下载', { exact: true }).waitFor()
  }

  await page.evaluate(() => {
    localStorage.setItem('theme', 'light')
    localStorage.removeItem('yuxi_debug_mode')
  })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.goto(`${origin}/agent/${threadId}`)
  await openPanel()
  check(await plus.count() === 0, '已有开始页时应隐藏加号')
  check(await panel.getByRole('tab').count() === 1 && treeRequests.length === 0, '默认只展示开始页，文件树等到打开时读取')
  check(await action('协作详情').count() === 0 && await action('调试').count() === 0, '普通会话不显示协作和调试入口')
  await checkStartPosition()
  check(await divider.evaluate(node =>
    getComputedStyle(node.closest('.side-panel')).borderLeftWidth === '0px' &&
    getComputedStyle(node, '::before').width === '1px'
  ), '空闲分割线应绘制为独立的 1px 细线')
  await dragAt(0.05, -48, 'rgb(189, 191, 191)')
  await dragAt(0.95, 48, 'rgb(189, 191, 191)')
  await checkTabColors('rgb(239, 242, 242)')
  const initialWidth = (await panel.boundingBox()).width
  for (const [key, width] of [['ArrowLeft', initialWidth + 24], ['ArrowRight', initialWidth]]) {
    await divider.press(key)
    await page.waitForFunction(expected =>
      Math.abs(document.getElementById('agent-file-panel').getBoundingClientRect().width - expected) < 1,
    width, { timeout: 2000 })
  }

  await choose('工作区文件')
  try {
    await projectTree.getByRole('status').getByText('正在加载文件系统...').waitFor({ timeout: 3000 })
    check(await projectTree.getByText('项目文件夹为空', { exact: true }).count() === 0, '项目根请求返回前不能显示空目录')
  } finally {
    firstProjectLoad.resolve()
  }
  await row(projectTree, projectEntry).waitFor()
  check(!treeRequests.includes('personal') && await personalTree.count() === 0, '项目标签只读取和展示项目文件')
  await choose('工作区文件')
  await plus.click()
  check(await isSelected(start) && await start.count() === 1 && await plus.count() === 0, '加号创建并聚焦唯一的开始页')
  await action('关闭 开始').click()
  check(await start.count() === 0 && await isSelected(projectTab), '手动关闭开始页应回到已有标签')

  await choose('个人空间文件')
  try {
    await personalTree.getByText('正在加载个人空间...', { exact: true }).waitFor()
  } finally {
    allowPersonalLoad()
  }
  await row(personalTree, personalEntry).waitFor()
  check(await projectTree.count() === 0, '个人空间标签独立展示')
  await choose('个人空间文件')
  await checkPlusPosition()
  for (const [fileTab, tree, scope] of [[projectTab, projectTree, '项目'], [personalTab, personalTree, '个人']]) {
    await fileTab.click()
    await row(tree, sharedEntry).click()
    await showPreview(`${scope}文件正文 ${sharedEntry.path}`)
  }
  const previewRequestsBeforeMaximize = previews.length
  await action('最大化面板').click()
  await action('还原面板').waitFor()
  await showPreview(`个人文件正文 ${sharedEntry.path}`)
  await action('还原面板').click()
  await action('最大化面板').waitFor()
  await showPreview(`个人文件正文 ${sharedEntry.path}`)
  check(previews.length === previewRequestsBeforeMaximize, '最大化与还原应保留当前预览')
  async function searchAndPreview(fileTab, actionName, entry, scope) {
    await fileTab.click()
    await action(actionName).click()
    const dialog = page.getByRole('dialog', { name: '搜索文件' })
    await dialog.getByRole('textbox', { name: '搜索文件' }).fill('搜索结果')
    await dialog.getByRole('button', { name: new RegExp(entry.name) }).click()
    await showPreview(`${scope}文件正文 ${entry.path}`)
  }
  await searchAndPreview(projectTab, '搜索项目文件夹', projectSearchEntry, '项目')
  await searchAndPreview(personalTab, '搜索个人空间', personalSearchEntry, '个人')
  check(JSON.stringify(searches) === JSON.stringify([['project', '搜索结果'], ['personal', '搜索结果']]), '搜索应送往对应作用域')
  for (const [scope, method] of [['project', 'GET'], ['personal', 'POST']]) {
    check(previews.some(entry => entry[0] === scope && entry[1] === method && entry[2] === sharedEntry.path), '同名文件应保持来源身份')
  }
  await projectTab.click()
  projectEmpty = true
  await action('刷新项目文件夹').click()
  await projectTree.getByText('项目文件夹为空', { exact: true }).waitFor()
  await personalTab.click()
  personalError = true
  await action('刷新个人空间').click()
  await personalTree.getByText('服务器内部错误，请稍后重试', { exact: true }).waitFor()
  personalError = false
  await personalTree.getByRole('button', { name: '重试', exact: true }).click()
  await row(personalTree, personalEntry).waitFor()

  const blocked = { started: Promise.withResolvers(), finish: Promise.withResolvers() }
  blockedPersonalRefresh = blocked
  try {
    await action('刷新个人空间').click()
    await blocked.started.promise
    await action('刷新个人空间').click()
  } finally {
    blocked.finish.resolve()
  }
  await row(personalTree, personalEntry).waitFor()
  check(await personalTree.getByRole('button', { name: '重试', exact: true }).count() === 0, '合并刷新成功后应清除前一请求的错误')

  await projectTab.click()
  const more = page.getByRole('button', { name: `更多操作：${artifact.name}`, exact: true })
  const downloadItem = page.getByRole('menuitem', { name: '下载', exact: true })
  const saveItem = page.getByRole('menuitem', { name: '保存到个人空间', exact: true })
  const waitForFocus = async (locator) => {
    await page.waitForFunction(element => document.activeElement === element, await locator.elementHandle(), { timeout: 3000 })
  }
  await more.press('Enter')
  await downloadItem.waitFor({ timeout: 3000 })
  await waitForFocus(downloadItem)
  await page.keyboard.press('ArrowDown')
  await waitForFocus(saveItem)
  await page.keyboard.press('Escape')
  await saveItem.waitFor({ state: 'hidden', timeout: 3000 })
  await waitForFocus(more)
  await more.press('ArrowDown')
  await waitForFocus(downloadItem)
  const downloaded = page.waitForEvent('download', { timeout: 3000 })
  const originalArtifact = page.waitForResponse(response => {
    const { path, params } = endpointParts(response.url())
    return decodeURI(path).endsWith(`/artifacts${artifactPath}`) && params.download === 'true'
  }, { timeout: 3000 })
  await page.keyboard.press('Enter')
  check((await downloaded).suggestedFilename() === 'artifact.txt' && (await originalArtifact).ok(), '键盘下载应请求交付物原件并使用响应文件名')
  await waitForFocus(more)
  await more.press('Space')
  await waitForFocus(downloadItem)
  await page.keyboard.press('ArrowDown')
  await waitForFocus(saveItem)
  await saveItem.press('Enter')
  const confirmArtifactSave = async (sourceId, entry) => {
    const personalReads = treeRequests.filter(scope => scope === 'personal').length
    const saveDialog = page.getByRole('dialog', { name: '保存交付物' })
    await saveDialog.getByRole('button', { name: /^保\s*存$/ }).click()
    await saveDialog.waitFor({ state: 'hidden' })
    check(JSON.stringify(saves.at(-1)) === JSON.stringify({
      threadId: sourceId, path: artifactSourcePath(entry), destination_path: savedDirectory.path
    }), '保存请求应绑定来源会话和个人空间目标')
    await row(personalTree, savedDirectory).waitFor({ timeout: 3000 })
    check(await isSelected(personalTab), '保存成功后应切到个人空间文件树')
    await row(personalTree, savedDirectory).click({ timeout: 3000 })
    await row(personalTree, entry).waitFor({ timeout: 3000 })
    check(treeRequests.filter(scope => scope === 'personal').length === personalReads + 1, '一次保存应只刷新一次个人空间根目录')
  }
  await confirmArtifactSave(threadId, artifact)

  const activeClose = panel.locator('.section-tab.active .section-tab-close')
  while (await activeClose.count()) await activeClose.click()
  check(await panel.getByRole('tab').count() === 1 && await isSelected(start) && await plus.count() === 0, '关闭最后一个标签应回到开始页并隐藏加号')

  await page.setViewportSize({ width: 768, height: 860 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.evaluate(() => {
    localStorage.setItem('theme', 'dark')
    localStorage.setItem('yuxi_debug_mode', 'true')
  })
  memberExists = true
  userRole = 'superadmin'
  await page.reload()
  await openPanel()
  await action('协作详情').waitFor()
  await action('调试').waitFor()
  check(await page.locator('.chat-header').getByRole('button', { name: 'Debug', exact: true }).count() === 0, '聊天顶部不应显示 Debug 按钮')
  check(await page.locator('html').evaluate(node => node.classList.contains('dark')), '窄屏截图应为暗色')
  const bounds = await panel.boundingBox()
  check(bounds.x >= 0 && bounds.x + bounds.width <= 769, '窄屏侧栏应位于视口内')
  check(await plus.count() === 0, '暗色窄屏也应隐藏开始页旁的加号')
  check(await divider.evaluate(node => getComputedStyle(node, '::before').transitionDuration === '0s'), '减少动效时分割线应即时切换')
  await checkStartPosition()
  await dragAt(0.05, -32, 'rgb(151, 153, 153)')
  await dragAt(0.95, 32, 'rgb(151, 153, 153)')
  await checkTabColors('rgb(52, 56, 61)')
  await page.mouse.move(320, 100)
  await choose('协作详情')
  await panel.getByText('资料整理', { exact: true }).waitFor()
  await choose('协作详情')
  await choose('调试')
  await panel.locator('.message-debug-panel').waitFor()
  await choose('调试')
  await action('关闭 调试').click()
  check(await tab('调试').count() === 0, '调试标签应可关闭')

  await page.evaluate(() => localStorage.removeItem('yuxi_debug_mode'))
  await page.reload()
  await openPanel()
  await action('协作详情').waitFor()
  check(await action('调试').count() === 0 && await tab('协作详情').count() === 0, '未开启调试不显示入口，协作标签按需打开')
  await choose('协作详情')
  await panel.getByRole('button', { name: '打开会话 资料整理', exact: true }).click()
  await panel.getByRole('button', { name: `更多操作：${memberArtifact.name}`, exact: true }).click()
  await saveItem.click()
  await confirmArtifactSave(memberId, memberArtifact)
  await page.setViewportSize({ width: 1440, height: 930 })
  const secondAccountFile = file('新账号私有文件.txt')
  const delayedOldFile = file('旧账号迟到文件.txt')
  accountSwap = {
    previous: { started: Promise.withResolvers(), finish: Promise.withResolvers() },
    next: { started: Promise.withResolvers(), finish: Promise.withResolvers() },
    attachmentRefresh: { started: Promise.withResolvers(), finish: Promise.withResolvers() },
    nextFile: secondAccountFile,
    previousFile: delayedOldFile
  }
  try {
    await action('刷新个人空间').click()
    await accountSwap.previous.started.promise
    await more.click()
    await saveItem.click()
    const oldSaveDialog = page.getByRole('dialog', { name: '保存交付物' })
    await oldSaveDialog.getByRole('button', { name: /^保\s*存$/ }).click()
    await oldSaveDialog.waitFor({ state: 'hidden' })
    await accountSwap.attachmentRefresh.started.promise
    await page.evaluate(token => {
      const app = document.querySelector('#app').__vue_app__
      app.config.globalProperties.$pinia._s.get('user').applySession({
        access_token: token, user_id: 2, uid: 'second-account', username: '第二个测试账号', role: 'user'
      })
    }, replacementToken)
    const oldRefresh = page.waitForResponse(response =>
      response.url().endsWith(`/sessions/${threadId}/attachments`) &&
      response.request().headers().authorization !== `Bearer ${replacementToken}`
    )
    accountSwap.attachmentRefresh.finish.resolve()
    await (await oldRefresh).finished()
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    check(await page.locator('#agent-file-panel').getAttribute('aria-hidden') === 'true', '旧账号保存完成后不应打开新账号的侧栏')
    await openPanel()
    await choose('个人空间文件')
    await accountSwap.next.started.promise
    await personalTree.getByText('正在加载个人空间...', { exact: true }).waitFor()
    check(await row(personalTree, personalEntry).count() === 0, '新账号加载时显示了旧目录缓存')
    accountSwap.previous.finish.resolve()
    accountSwap.next.finish.resolve()
    await row(personalTree, secondAccountFile).waitFor()
    check(await row(personalTree, delayedOldFile).count() === 0, '旧账号在途结果进入了新目录')
  } finally {
    accountSwap.attachmentRefresh.finish.resolve()
    accountSwap.previous.finish.resolve()
    accountSwap.next.finish.resolve()
  }
  return { searches, previews, saves, singleStart: true, conditionalTools: true, projectLoading: true, keyboardArtifactMenu: true, accountScoped: true }
}
