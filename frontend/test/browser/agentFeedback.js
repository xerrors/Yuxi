// 已登录的合成验收会话：window.__feedbackFixture = { targets: [{ session_id, turn_id, run_id, agent_id }, ...] }。
// 三个目标须有公开的最终输出，第三个目标须有长篇 Agent 标记；仅第一个目标的评价会被修改。
// playwright-cli --raw -s=<session> run-code --filename=frontend/test/browser/agentFeedback.js
// prettier-ignore
async (page) => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = await page.evaluate(() => window.location.origin)
  const fixture = await page.evaluate(() => window.__feedbackFixture)
  check(fixture?.targets?.length >= 3, '请先准备独立的合成反馈验收会话')
  await page.unrouteAll({ behavior: 'wait' })
  const target = fixture.targets[0]
  const feedbackPath = `/api/agent/threads/${target.session_id}/turns/${target.turn_id}/feedback`
  const request = (path, method = 'GET') => page.evaluate(async ({ path, method }) => {
    const response = await fetch(path, { method, headers: { Authorization: `Bearer ${localStorage.getItem('user_token')}` } })
    if (!response.ok) throw new Error(`验收接口失败 ${response.status}`)
    return response.status === 204 ? null : response.json()
  }, { path, method })
  const persisted = async () => (await request(`/api/agent/threads/${target.session_id}/feedback?source=user&run_ids=${target.run_id}`)).items
  const dialog = page.getByRole('dialog', { name: '补充反馈' })
  const like = page.locator('button.feedback-vote[aria-label="点赞"]')
  const dislike = page.locator('button.feedback-vote[aria-label="点踩"]')
  const textarea = dialog.getByRole('textbox', { name: '补充说明 选填' })
  const closed = () => dialog.waitFor({ state: 'hidden' })
  const waitFrame = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  const detailsVisible = () => page.waitForFunction(() => {
    const bounds = document.querySelector('.ant-modal-content')?.getBoundingClientRect()
    return bounds && bounds.left >= 0 && bounds.right <= window.innerWidth + 1
  })

  await page.setViewportSize({ width: 1440, height: 1000 })
  await request(`${feedbackPath}?run_id=${target.run_id}`, 'DELETE')
  await page.goto(`${origin}/agent/${target.session_id}`)
  await like.waitFor()
  await page.waitForFunction(() => !document.querySelector('button.feedback-vote[aria-label="点赞"]')?.disabled)
  await like.click()
  await dialog.waitFor()
  await page.waitForFunction(() => document.activeElement?.classList.contains('preset-option'))
  check(await textarea.inputValue() === '', '首次补充说明应为空')
  check(!await dialog.getByRole('button', { name: '提交反馈', exact: true }).isDisabled(), '空说明不应禁用保存')
  await page.keyboard.press('Escape')
  await closed()
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === '点赞', null, { timeout: 5000 })
    .catch(() => { throw new Error('关闭弹窗后焦点未回到点赞按钮') })
  check((await persisted())[0]?.rating === 'like', '关闭弹窗不应撤销已提交评价')

  check(await page.getByRole('button', { name: /^(补充说明|编辑说明)$/ }).count() === 0, '评价后不能留下补充或编辑入口')
  await like.click()
  await page.waitForFunction(() => document.querySelector('button.feedback-vote[aria-label="点赞"]')?.getAttribute('aria-pressed') === 'false')
  await like.click()
  await dialog.waitFor()
  await dialog.getByRole('button', { name: '解决了我的问题', exact: true }).click()
  await textarea.fill('先确认验收目标，再按最小步骤执行。')
  for (let index = 0; index < 8; index++) {
    await page.keyboard.press('Tab')
    check(await dialog.evaluate(el => el.contains(document.activeElement)), 'Tab 焦点不能离开说明弹窗')
  }
  await page.route(`**${feedbackPath}`, route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 503, json: { detail: '暂时无法保存反馈' } }) : route.continue())
  await dialog.getByRole('button', { name: '提交反馈', exact: true }).click()
  await dialog.getByRole('alert').waitFor()
  await page.waitForFunction(() => document.activeElement?.getAttribute('role') === 'alert')
  check(await textarea.inputValue() === '先确认验收目标，再按最小步骤执行。', '保存失败不能丢失草稿')
  check((await persisted())[0]?.reason === null, '失败请求不能写入说明')
  await page.locator('.ant-modal-content').screenshot({ animations: 'disabled', path: '/tmp/yuxi-feedback-ui-modal-error.png' })
  await page.unroute(`**${feedbackPath}`)
  await dialog.getByRole('button', { name: '提交反馈', exact: true }).click()
  await closed()
  check((await persisted())[0]?.reason === '解决了我的问题\n\n先确认验收目标，再按最小步骤执行。', '重试后应提交原草稿')
  await like.click()
  await page.waitForFunction(() => document.querySelector('button.feedback-vote[aria-label="点赞"]')?.getAttribute('aria-pressed') === 'false')
  check((await persisted()).length === 0, '再次点击同一评价应撤销')
  check(!await dialog.isVisible(), '撤销不应打开说明弹窗')
  await dislike.click()
  await dialog.waitFor()
  check(await dialog.getByText('已点踩', { exact: true }).isVisible(), '弹窗应与已保存的评价一致')
  for (const theme of ['light', 'dark']) {
    await page.keyboard.press('Escape')
    await closed()
    await page.evaluate(theme => localStorage.setItem('theme', theme), theme)
    await page.reload()
    await dislike.click()
    await page.waitForFunction(() => document.querySelector('button.feedback-vote[aria-label="点踩"]')?.getAttribute('aria-pressed') === 'false')
    await dislike.click()
    await dialog.waitFor()
    check(await page.locator('html').evaluate(el => el.classList.contains('dark')) === (theme === 'dark'), '主题应正确恢复')
    await page.locator('.ant-modal-content').screenshot({ animations: 'disabled', path: `/tmp/yuxi-feedback-ui-modal-${theme}.png` })
  }
  await page.setViewportSize({ width: 375, height: 812 })
  await waitFrame()
  check(await page.locator('.ant-modal-content').evaluate(el => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().right <= window.innerWidth), '窄屏弹窗不应横向溢出')
  await page.locator('.ant-modal-content').screenshot({ animations: 'disabled', path: '/tmp/yuxi-feedback-ui-modal-mobile.png' })
  await page.keyboard.press('Escape')
  await closed()
  await page.reload()
  await page.waitForFunction(() => document.querySelector('button.feedback-vote[aria-label="点踩"]')?.getAttribute('aria-pressed') === 'true')

  const rewriteEmpty = items => {
    for (const item of items || []) {
      if (item.type === 'message' && item.phase === 'final_answer' && item.status === 'completed') {
        item.content = [{ type: 'output_text', text: '   ' }]
      }
    }
  }
  const sessionPattern = `**/api/v1/agents/sessions/${target.session_id}/**`
  await page.route(sessionPattern, async route => {
    const path = route.request().url().split('?')[0]
    if (route.request().method() !== 'GET' || (!path.endsWith('/items') && !path.includes('/turns/'))) return route.continue()
    const response = await route.fetch()
    const snapshot = await response.json()
    rewriteEmpty(snapshot.data)
    rewriteEmpty(snapshot.yuxi?.output)
    await route.fulfill({ response, json: snapshot })
  })
  await page.reload()
  await page.locator('.chat-inline-notice').filter({ hasText: '本次运行已结束' }).waitFor()
  await page.locator('.refs').first().waitFor()
  // 等历史记录与 Turn 快照完成投影，避免在后续快照恢复前误判为没有按钮。
  await page.waitForTimeout(800)
  check(await page.locator('button.feedback-vote').count() === 0, '空白最终输出不能开放评价')
  await page.unroute(sessionPattern)

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${origin}/dashboard?tab=feedback`)
  await page.locator('.feedback-record').first().waitFor()
  if (await page.locator('.app-layout.sidebar-collapsed').count()) {
    await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  }
  const chooseAgent = async name => {
    await page.locator('.agent-field .ant-select-selector').click()
    await page.locator('.ant-select-item-option').filter({ hasText: name }).click()
    await page.keyboard.press('Escape')
    await page.waitForFunction(() => document.querySelectorAll('.feedback-record').length && document.querySelector('.feedback-records')?.getAttribute('aria-busy') === 'false')
  }
  await chooseAgent('需求分析助手')
  const initialRows = await page.locator('.feedback-record').count()
  check(initialRows >= 4, '应同时展示用户反馈与 Agent 标记')
  const firstDetails = page.locator('.record-details').first()
  await firstDetails.click()
  const details = page.getByRole('dialog', { name: '反馈详情' })
  await details.waitFor()
  await detailsVisible()
  check(await page.getByRole('dialog', { name: '反馈详情' }).getAttribute('aria-modal') === 'true', '详情应有弹窗语义')
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let index = 0; index < 6; index++) {
      await page.keyboard.press(key)
      check(await details.evaluate(el => el.contains(document.activeElement)), `详情焦点不能离开弹窗 ${key}`)
    }
  }
  await page.keyboard.press('Escape')
  await details.waitFor({ state: 'hidden' })
  await page.waitForFunction(() => document.activeElement?.classList.contains('record-details'))
  await firstDetails.click()
  await detailsVisible()
  check(await details.getByText('执行 ID', { exact: true }).count() === 0, '详情不展示内部执行标识')
  await page.route(`**/api/v1/agents/sessions/${target.session_id}`, route => route.fulfill({ status: 404, json: { detail: 'Session 不存在' } }))
  await details.getByRole('button', { name: '查看原会话', exact: true }).click()
  await details.getByRole('alert').filter({ hasText: '无会话访问权限' }).waitFor()
  check(new URL(page.url()).searchParams.get('tab') === 'feedback', '原会话拒绝时应留在反馈详情')
  await page.unroute(`**/api/v1/agents/sessions/${target.session_id}`)
  await details.locator('.ant-modal-close').click()
  await details.waitFor({ state: 'hidden' })
  await page.waitForFunction(() => document.activeElement?.classList.contains('record-details'), null, { timeout: 5000 })
    .catch(() => { throw new Error('关闭详情后焦点未回到详情按钮') })

  for (const width of [1440, 1024, 1001, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.waitForFunction(width => {
      const bounds = document.querySelector('.feedback-list')?.getBoundingClientRect()
      return bounds && bounds.right <= window.innerWidth + 1 && bounds.left >= 0 && (width > 767 || bounds.left <= 53)
    }, width)
    check(await page.locator('.feedback-record').evaluateAll(rows => rows.every(el => {
      const panel = el.closest('.feedback-panel').getBoundingClientRect()
      const button = el.querySelector('.record-details').getBoundingClientRect()
      return el.scrollWidth <= el.clientWidth && button.right <= panel.right && button.left >= panel.left
    })), `列表操作应完整可见 ${width}`)
    check(await page.locator('.feedback-list').evaluate(el => el.scrollWidth <= el.clientWidth), `反馈筛选不得溢出 ${width}`)
  }
  await page.locator('.system-manage-content > .tab-panel:visible').evaluate(el => { el.scrollTop = 0 })
  const mobileBounds = await page.locator('.feedback-list').boundingBox()
  await page.screenshot({ animations: 'disabled', path: '/tmp/yuxi-feedback-ui-admin-mobile.png', clip: {
    ...mobileBounds, height: Math.min(mobileBounds.height, page.viewportSize().height - mobileBounds.y)
  } })
  await page.setViewportSize({ width: 1440, height: 1000 })
  for (const theme of ['light', 'dark']) {
    if (await page.locator('html').evaluate(el => el.classList.contains('dark')) !== (theme === 'dark')) {
      await page.getByRole('button', { name: '切换主题', exact: true }).click()
    }
    await page.waitForFunction(dark => {
      const background = getComputedStyle(document.querySelector('.agent-field .ant-select-selector')).backgroundColor
      const channel = Number(background.match(/\d+/)?.[0])
      return dark ? channel < 100 : channel > 200
    }, theme === 'dark')
    await page.locator('.feedback-count').hover()
    await page.locator('.ant-tooltip:visible').waitFor({ state: 'hidden' })
    await page.locator('.system-manage-content > .tab-panel:visible').evaluate(el => { el.scrollTop = 0 })
    await page.locator('.feedback-list').screenshot({ animations: 'disabled', path: `/tmp/yuxi-feedback-ui-admin-${theme}.png` })
  }
  await page.locator('.source-field .ant-select-selector').click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '用户评价' }).click()
  await page.locator('.rating-field').waitFor()
  await page.waitForFunction(() => document.querySelectorAll('.feedback-record').length === 2)
  await page.locator('.rating-field .ant-select-selector').click()
  await page.locator('.ant-select-item-option').filter({ hasText: '正向评价' }).click()
  await page.keyboard.press('Escape')
  await page.waitForFunction(() => document.querySelectorAll('.feedback-record').length === 1)
  await page.locator('.source-field .ant-select-selector').click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: '智能体标记' }).click()
  await page.waitForFunction(() => document.querySelectorAll('.feedback-record').length === 3)
  check(await page.locator('.rating-field').count() === 0, 'Agent 来源不能保留用户评价筛选')
  await chooseAgent('代码排查助手')
  await page.locator('.record-details').first().click()
  await details.waitFor()
  await detailsVisible()
  check((await details.locator('.detail-reason').textContent()).length > 3000, '详情应展示完整长篇说明')
  await details.locator('.ant-modal-body').evaluate(el => { el.scrollTop = el.scrollHeight })
  await page.keyboard.press('Escape')
  await details.waitFor({ state: 'hidden' })
  await page.locator('.record-details').first().click()
  await details.waitFor()
  await page.waitForFunction(() => document.activeElement?.id === 'feedback-content-label')
  check(await details.locator('.ant-modal-body').evaluate(el => el.scrollTop) === 0, '重新打开详情应从正文顶部开始')
  await page.locator('.ant-modal-content').screenshot({ animations: 'disabled', path: '/tmp/yuxi-feedback-ui-detail.png' })
  await page.setViewportSize({ width: 375, height: 812 })
  await page.waitForFunction(() => document.querySelector('.ant-modal-content')?.getBoundingClientRect().width <= window.innerWidth)
  check(await details.evaluate(el => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().width <= window.innerWidth), '窄屏详情不应溢出')
  await details.locator('.ant-modal-close').click()
  await details.waitFor({ state: 'hidden' })
  await page.setViewportSize({ width: 1440, height: 1000 })

  // 分页与故障使用明确的协议响应；实际写入仍走上面的真实 API。
  const baseline = await request(`/api/system/agent-feedback?limit=50&agent_id=${target.agent_id}`)
  const sample = baseline.items[0]
  let mode = 'pagination', failures = 1
  const cursors = []
  await page.route('**/api/system/agent-feedback?*', route => {
    const after = route.request().url().match(/[?&]after=([^&]*)/)?.[1] || null
    cursors.push(after)
    if (mode === 'empty') return route.fulfill({ json: { items: [], has_more: false, last_id: null } })
    if (mode === 'error' || (after && failures-- > 0)) return route.fulfill({ status: 503, json: { detail: '服务暂不可用' } })
    return route.fulfill({ json: {
      items: [{ ...sample, id: after ? sample.id + 1 : sample.id, reason: after ? '第二页记录' : '第一页记录' }],
      has_more: !after, last_id: after ? sample.id + 1 : sample.id
    } })
  })
  await page.locator('.feedback-toolbar').getByRole('button', { name: '刷新', exact: true }).click()
  await page.getByText('第一页记录', { exact: true }).waitFor()
  await page.getByRole('button', { name: '加载更多', exact: true }).click()
  await page.locator('.page-error').waitFor()
  check(await page.getByText('第一页记录', { exact: true }).isVisible(), '后页失败不能清除已有记录')
  await page.locator('.page-error').getByRole('button', { name: '重新加载', exact: true }).click()
  await page.getByText('第二页记录', { exact: true }).waitFor()
  check(cursors.at(-1) === cursors.at(-2) && cursors.at(-1) !== null, '重试应使用同一后页游标')
  check(await page.locator('.feedback-record').count() === 2, '重试成功应追加记录')
  mode = 'empty'
  await page.locator('.feedback-toolbar').getByRole('button', { name: '刷新', exact: true }).click()
  await page.getByText('无匹配记录', { exact: true }).waitFor()
  mode = 'error'
  await page.locator('.feedback-toolbar').getByRole('button', { name: '刷新', exact: true }).click()
  await page.getByText('反馈加载失败', { exact: true }).first().waitFor()
  await page.unrouteAll({ behavior: 'wait' })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(`${origin}/dashboard?tab=feedback`)
  await page.locator('.feedback-record').first().waitFor()
  await chooseAgent('需求分析助手')
  return { optionalModal: true, focusAndRetry: true, persistedRating: 'dislike', sourceFilters: true, paginationRetry: true, widths: [1440, 1024, 1001, 768, 375] }
}
