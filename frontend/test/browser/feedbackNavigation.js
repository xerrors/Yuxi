// 已登录 5173，window.__feedbackFixture.targets[0] 指向自己的已评价会话；请求延迟仅在浏览器内注入。
// playwright-cli --raw -s=<session> run-code --filename=frontend/test/browser/feedbackNavigation.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = await page.evaluate(() => window.location.origin)
  const target = await page.evaluate(() => window.__feedbackFixture?.targets?.[0])
  check(target?.session_id && target?.agent_id, '请提供自己的反馈验收会话')
  const sessionPath = '/api/v1/agents/sessions/' + target.session_id
  const feedbackBase = '/api/agent/threads/' + target.session_id
  const read = path => page.evaluate(async path => {
    const response = await fetch(path, { headers: { Authorization: 'Bearer ' + localStorage.getItem('user_token') } })
    if (!response.ok) throw new Error('验收数据读取失败：' + response.status)
    return response.json()
  }, path)
  const session = await read(sessionPath)
  const records = (await read(feedbackBase + '/feedback?source=user')).items
  const record = records.find(item => !target.run_id || item.run_id === target.run_id)
  check(record, '目标必须已有一条已保存的评价')
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(origin + '/dashboard?tab=feedback')
  await page.locator('.feedback-list').waitFor()
  if (await page.locator('.app-layout.sidebar-collapsed').count()) {
    await page.getByRole('button', { name: '展开侧边栏', exact: true }).click()
  }
  await page.getByRole('button', { name: session.yuxi.title, exact: true }).click()
  await page.waitForURL(origin + '/agent/' + target.session_id)

  // 保存响应迟到时，记录可以恢复，离页前的弹窗意图不能恢复。
  const rating = record.rating === 'like' ? 'dislike' : 'like'
  const vote = page.getByRole('button', { name: rating === 'like' ? '点赞' : '点踩', exact: true })
  const feedbackPath = feedbackBase + '/turns/' + record.turn_id + '/feedback'
  let releaseVote
  const delayedVote = new Promise(resolve => { releaseVote = resolve })
  await page.route('**' + feedbackPath, async route => {
    await delayedVote
    await route.fulfill({ json: { ...record, rating, reason: null } })
  })
  const requested = page.waitForRequest(request => request.method() === 'PUT' && request.url().endsWith(feedbackPath))
  await vote.click()
  await requested
  await page.goBack()
  await page.waitForURL(origin + '/system-manage?tab=feedback')
  await page.goForward()
  await page.waitForURL(origin + '/agent/' + target.session_id)
  await vote.waitFor()
  releaseVote()
  await page.waitForFunction(label => document.querySelector(`button.feedback-vote[aria-label="${label}"]`)?.getAttribute('aria-pressed') === 'true', rating === 'like' ? '点赞' : '点踩')
  await page.waitForTimeout(300)
  check(await page.getByRole('dialog', { name: '补充反馈' }).count() === 0, '返回缓存会话后，迟到保存不能重新打开弹窗')
  await page.unroute('**' + feedbackPath)
  await page.reload()
  const restored = (await read(feedbackBase + '/feedback?source=user')).items.find(item => item.id === record.id)
  check(restored?.rating === record.rating && restored?.reason === record.reason, '延迟响应注入不能改动真实评价')

  const openOwnDetails = async () => {
    await page.locator('.agent-field .ant-select-selector').click()
    const options = page.locator('.ant-select-item-option')
    const own = options.filter({ hasText: record.agent_name || record.agent_id })
    await own.click()
    await page.keyboard.press('Escape')
    await page.waitForFunction(() => document.querySelector('.feedback-records')?.getAttribute('aria-busy') === 'false')
    const rows = page.locator('.feedback-record').filter({ hasText: record.reason || '无补充说明' })
    for (let index = 0; index < await rows.count(); index++) {
      await rows.nth(index).locator('.record-details').click()
      const modal = page.getByRole('dialog', { name: '反馈详情' })
      await modal.waitFor()
      if ((await modal.locator('.detail-reason').textContent()).trim() === (record.reason || '无补充说明')) return
      await page.keyboard.press('Escape')
      await modal.waitFor({ state: 'hidden' })
    }
    throw new Error('未找到目标评价的详情')
  }
  const details = page.getByRole('dialog', { name: '反馈详情' })
  await page.goto(origin + '/dashboard?tab=feedback')
  await openOwnDetails()
  await details.getByRole('button', { name: '查看原会话', exact: true }).click()
  await page.waitForURL(origin + '/agent/' + target.session_id)
  await details.waitFor({ state: 'hidden' })
  await page.goBack()
  await page.waitForURL(origin + '/system-manage?tab=feedback')
  check(await details.count() === 0, '返回缓存列表时详情应保持关闭')

  // 查询原会话期间切换页面或销毁反馈标签，迟到成功不能重新接管导航。
  for (const leave of ['page', 'tab']) {
    if (leave === 'tab') await page.goto(origin + '/dashboard?tab=feedback')
    await openOwnDetails()
    let releaseSession
    const delayedSession = new Promise(resolve => { releaseSession = resolve })
    await page.route('**' + sessionPath, async route => {
      await delayedSession
      await route.fulfill({ json: session })
    })
    const sessionRequested = page.waitForRequest(request => request.method() === 'GET' && request.url().endsWith(sessionPath))
    await details.getByRole('button', { name: '查看原会话', exact: true }).click()
    await sessionRequested
    await details.locator('.ant-modal-close').click()
    await details.waitFor({ state: 'hidden' })
    if (leave === 'page') await page.getByRole('link', { name: '智能体', exact: true }).click()
    else await page.getByRole('button', { name: '系统概览', exact: true }).click()
    const destination = origin + (leave === 'page' ? '/agent-manage' : '/system-manage?tab=overview')
    await page.waitForURL(destination)
    const sessionReplied = page.waitForResponse(response => response.url().endsWith(sessionPath))
    releaseSession()
    await sessionReplied
    await page.waitForTimeout(300)
    check(page.url() === destination, '离开反馈列表后，迟到查询不能跳回原会话：' + leave)
    check(await details.count() === 0, '离页后不能留下详情弹窗')
    await page.unroute('**' + sessionPath)
  }
  return { pendingVoteDoesNotReopen: true, storedFeedbackUnchanged: true, sessionNavigationClosesDetails: true, lateSessionDoesNotNavigate: true, destroyedTabDoesNotNavigate: true }
}
