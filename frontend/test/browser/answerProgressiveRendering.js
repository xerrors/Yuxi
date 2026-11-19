// 真实 5173 页面，挂起渲染资源，验证会话正文能在格式化前阅读。
// prettier-ignore
async page => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const check = (value, message) => { if (!value) throw new Error(message) }
  const title = '历史回答在加载格式时可读'
  const plain = '普通回答已经可以阅读。'
  const withCode = '带代码的回答已经可以阅读。'
  const code = 'const value = 42'
  const rawTag = '<img src="/favicon.svg" alt="测试原文">'
  const fenced = [withCode, '', '~~~js', code, '~~~', '', '数学公式 $x^2$'].join('\n')
  const agent = { agent_id: 'answer-test', name: '会话助手', can_run: true, config_json: { context: {} }, configurable_items: {} }
  const thread = { id: 'answer-history', agent: { id: agent.agent_id }, status: 'completed',
    yuxi: { title, current_turn: null, project_id: null, config: {} } }
  const items = [plain + '\n\n' + rawTag, '继续说明', withCode].map((text, i) => {
    const role = i === 1 ? 'user' : 'assistant'
    return { id: 'msg-' + i, turn_id: 'turn-' + i, type: 'message', role, status: 'completed',
      content: [{ type: role === 'user' ? 'input_text' : 'output_text', text }],
      yuxi: { message_id: i + 1, run_id: 'run-' + i, created_at: '2026-11-01T00:00:00Z' } }
  })
  await page.addInitScript(() => localStorage.setItem('user_token', 'answer-progressive-fixture'))
  await page.route('**/api/**', async route => {
    const path = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    let json = { data: [], providers: [], models: [], files: [], skills: [], config: {}, first_run: false }
    if (path === '/api/system/info') json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    else if (path === '/api/auth/me') json = { id: 1, uid: 'answer-fixture', username: '阅读验证', role: 'user' }
    else if (path === '/api/agent') json = { agents: [agent] }
    else if (path === '/api/agent/answer-test') json = agent
    else if (path === '/api/projects' || path === '/api/mention/plugins') json = []
    else if (path.endsWith('/sessions')) json = { data: [thread], has_more: false }
    else if (path.endsWith('/answer-history') || path.endsWith('/viewed')) json = thread
    else if (path.endsWith('/items')) json = { data: [...items].reverse(), has_more: false, yuxi: { runs: [] } }
    else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    else if (path.endsWith('/queue')) json = { inputs: [], status: 'ready' }
    else if (path.endsWith('/cooperation')) json = { sessions: [] }
    else if (path.endsWith('/attachments')) json = { attachments: [] }
    await route.fulfill({ json })
  })
  let releaseMarkdown, releaseShiki
  const markdownGate = new Promise(resolve => { releaseMarkdown = resolve })
  const shikiGate = new Promise(resolve => { releaseShiki = resolve })
  let offline = false
  await page.route(/\/MarkdownPreview\.vue(?:\?|$)/, async route => {
    if (offline) return route.abort('failed')
    await markdownGate
    return route.continue()
  })
  await page.route(/\/shiki(?:[._/?-]|$)/, async route => {
    await shikiGate
    return route.continue()
  })
  const chat = page.locator('.agent-view .chat-main')
  const fallback = chat.locator('.answer-text-fallback')
  await page.goto(origin + '/agent/answer-history')
  await chat.getByText('继续说明', { exact: true }).waitFor()
  await chat.getByText(plain, { exact: false }).waitFor({ timeout: 4000 })
  await fallback.filter({ hasText: plain }).waitFor({ timeout: 4000 })
  await fallback.filter({ hasText: withCode }).waitFor({ timeout: 4000 })
  check((await fallback.first().innerText()).includes(rawTag), '纯文本未保留原文')
  check(await fallback.locator('img').count() === 0, '纯文本占位解析了 HTML')

  releaseMarkdown()
  await chat.locator('.yk-markdown-preview').getByText(plain, { exact: true }).waitFor()
  await chat.locator('.yk-markdown-preview img[alt="测试原文"]').waitFor({ state: 'attached' })
  await chat.locator('.yk-markdown-preview').getByText(withCode, { exact: true }).waitFor()
  const shikiStarted = page.waitForRequest(/\/shiki(?:[._/?-]|$)/)
  await page.evaluate(async content => {
    const { useSessionRuntimeStore } = await import('/src/modules/session/model/sessionRuntime.js')
    const app = document.querySelector('#app').__vue_app__
    const runtime = useSessionRuntimeStore(app.config.globalProperties.$pinia)
    runtime.getThreadState('answer-history').itemState.items['msg-2'].content[0].text = content
  }, fenced)
  await shikiStarted
  await chat.getByText(code, { exact: false }).waitFor({ timeout: 4000 })
  await fallback.filter({ hasText: withCode }).waitFor()
  check(!await chat.locator('.yk-markdown-preview').filter({ hasText: withCode }).isVisible(), '重复显示了旧版 Markdown')
  releaseShiki()
  await chat.locator('pre.shiki').getByText(code, { exact: true }).waitFor()
  check(await fallback.count() === 0, '格式化后留有重复原文')

  offline = true
  items.at(-1).content[0].text = fenced
  await page.reload()
  await fallback.filter({ hasText: withCode }).waitFor()
  await chat.getByText('排版加载失败，已显示原文', { exact: true }).first().waitFor()
  return { readableWhileMarkdownLoads: true, readableWhileShikiLoads: true, richFormatting: true, readableAfterNetworkError: true }
}
