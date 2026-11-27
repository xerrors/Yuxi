// 在已登录、选好体验 Agent 的 5173 页面运行；真实创建一条对话并保留反馈供检查。
// playwright-cli --raw -s=<session> run-code --filename=frontend/test/browser/feedbackModalKeepAlive.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = await page.evaluate(() => window.location.origin)
  const prompt = '【反馈体验 2：点踩并说明】我正在做 Agent 反馈模块。请给我完整检查清单，覆盖点击、说明填写、撤销、刷新和管理员查看。每项一句话，不使用工具。'
  const reason = '内容偏长，我希望先看到最短的三步操作，再按需展开详细检查；补充说明保持选填即可。'
  await page.goto(origin + '/dashboard?tab=feedback')
  await page.getByRole('link', { name: '新建对话', exact: true }).click()
  await page.waitForURL(/\/agent$/)
  const accepted = page.waitForResponse(response => response.request().method() === 'POST' && /\/api\/v1\/agents\/sessions\/[^/]+\/events$/.test(response.url()))
  await page.locator('.mention-editor[contenteditable="true"]').fill(prompt)
  await page.getByRole('button', { name: '发送消息', exact: true }).click()
  check((await accepted).ok(), '真实输入应被接收')
  await page.waitForURL(/\/agent\/[^/?]+$/)
  const sessionId = page.url().split('?')[0].split('/').at(-1)
  const dislike = page.getByRole('button', { name: '点踩', exact: true })
  await dislike.waitFor({ timeout: 30000 })
  await dislike.click()
  const modal = page.getByRole('dialog', { name: '补充反馈' })
  await modal.waitFor()
  check(await modal.count() === 1, '缓存的新对话页不能同时打开第二个反馈弹窗')
  await modal.getByRole('textbox').fill(reason)
  await page.locator('.ant-modal-content').screenshot({ animations: 'disabled', path: '/tmp/yuxi-feedback-live-with-reason.png' })
  await modal.getByRole('button', { name: '提交反馈', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })

  check(await page.getByRole('button', { name: /^(编辑说明|补充说明)$/ }).count() === 0, '保存后不提供补充入口')
  await page.getByRole('button', { name: '点赞', exact: true }).click()
  await modal.waitFor()
  await modal.getByRole('textbox').fill('尚未提交的说明')
  await page.goBack()
  await page.waitForURL(/\/system-manage\?tab=feedback$/)
  await modal.waitFor({ state: 'hidden' })
  await page.goForward()
  await page.waitForURL(new RegExp('/agent/' + sessionId + '$'))
  check(await modal.count() === 0, '返回会话时不能自动恢复已关闭的弹窗')
  await dislike.click()
  await modal.waitFor()
  check(await modal.getByRole('textbox').inputValue() === '', '新的评价不带入上次未提交说明')
  await modal.getByRole('textbox').fill(reason)
  await modal.getByRole('button', { name: '提交反馈', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })

  const stored = await page.evaluate(async ({ sessionId, reason }) => {
    const headers = { Authorization: 'Bearer ' + localStorage.getItem('user_token'), 'Content-Type': 'application/json' }
    const renamed = await fetch('/api/v1/agents/sessions/' + sessionId, { method: 'POST', headers,
      body: JSON.stringify({ yuxi: { title: '反馈体验 2 · 点踩并填写说明' } }) })
    if (!renamed.ok) throw new Error('会话命名失败')
    const response = await fetch('/api/agent/threads/' + sessionId + '/feedback?source=user', { headers })
    if (!response.ok) throw new Error('反馈回读失败')
    const records = (await response.json()).items
    if (records.length !== 1 || records[0].rating !== 'dislike' || records[0].reason !== reason) throw new Error('点踩与说明未正确持久化')
    return { run_id: records[0].run_id, rating: records[0].rating, reason: records[0].reason }
  }, { sessionId, reason })
  await page.reload()
  await page.waitForFunction(() => document.querySelector('button.feedback-vote[aria-label="点踩"]')?.getAttribute('aria-pressed') === 'true')
  check(await page.getByRole('button', { name: /^(编辑说明|补充说明)$/ }).count() === 0, '刷新后也不提供补充入口')
  await page.locator('.chat-container').screenshot({ animations: 'disabled', path: '/tmp/yuxi-feedback-live-chat-second.png' })
  return { case: 2, session_id: sessionId, ...stored, one_modal: true, deactivated_modal_closed: true,  reload_restored: true }
}
