// 使用独立页面、合成 API 和隔离认证，核对真实会话组件的附件草稿归属。
// eslint-disable-next-line no-unused-vars -- playwright-cli 将文件作为函数表达式执行。
async function attachmentDraftOwnership(page) {
  const origin = await page.evaluate(() => location.origin)
  const probe = await page.context().newPage()
  const agentId = '00000000-0000-4000-8000-000000000003'
  const ids = ['00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002']
  const agent = {
    agent_id: agentId,
    name: '附件归属验证',
    config_json: { context: {} },
    configurable_items: {},
    metadata: {}
  }
  const sessions = ids.map((id, index) => ({
    id,
    object: 'agent.session',
    status: 'completed',
    agent: { id: agentId },
    created_at: 1791580000,
    metadata: {},
    yuxi: {
      title: `附件会话 ${index + 1}`,
      current_turn: null,
      context: {},
      project_id: 'attachment-project',
      config: {},
      is_pinned: false
    }
  }))
  const check = (condition, message) => {
    if (!condition) throw Error(message)
  }
  let releaseUpload
  const uploadGate = new Promise((resolve) => {
    releaseUpload = resolve
  })
  let uploadStarted = false
  const file = {
    id: 'attachment-from-a',
    object: 'file',
    filename: 'from-a.txt',
    bytes: 7,
    status: 'uploaded',
    parse_methods: []
  }
  try {
    await probe.addInitScript(() => {
      const values = new Map([['user_token', 'attachment-fixture']])
      Object.defineProperty(window, 'localStorage', {
        value: {
          getItem: (key) => values.get(key) ?? null,
          setItem: (key, value) => values.set(key, String(value)),
          removeItem: (key) => values.delete(key)
        }
      })
    })
    await probe.route('**/api/**', async (route) => {
      const request = route.request()
      const raw = request.url()
      const path = raw.replace(/^https?:\/\/[^/]+/, '').split('?')[0]
      let json
      if (path === '/api/auth/me')
        json = { id: 1, uid: 'attachment-fixture', username: '附件验证', role: 'user' }
      else if (path === '/api/agent') json = { agents: [agent] }
      else if (path === `/api/agent/${agentId}`) json = agent
      else if (path === '/api/mention/plugins') json = []
      else if (path === '/api/v1/agents/sessions')
        json = { data: raw.includes('is_pinned=true') ? [] : sessions, has_more: false }
      else if (path.endsWith('/items')) json = { data: [], has_more: false, yuxi: { runs: [] } }
      else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
      else if (path.endsWith('/queue')) json = { inputs: [], status: 'ready' }
      else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
      else if (path.endsWith('/attachments')) json = { files: [], attachments: [] }
      else if (path === '/api/v1/agents/files' && request.method() === 'POST') {
        uploadStarted = true
        await uploadGate
        json = file
      } else if (path === `/api/agent/files/${file.id}`) json = file
      else if (path === '/api/projects') json = []
      else if (path.includes('/databases')) json = { databases: [] }
      else if (path.includes('/models')) json = { providers: [], models: [], data: [] }
      else if (ids.some((id) => path.endsWith(`/${id}`) || path.endsWith(`/${id}/viewed`))) {
        json = sessions.find((session) => path.includes(session.id))
      } else
        json = {
          data: [],
          files: [],
          skills: [],
          projects: [],
          providers: [],
          config: {},
          site_name: '语析',
          first_run: false
        }
      await route.fulfill({ json })
    })
    await probe.setViewportSize({ width: 1440, height: 900 })
    await probe.goto(`${origin}/agent/${ids[0]}`)
    const input = probe.locator('.agent-view [contenteditable="true"]')
    await input.waitFor()
    await probe.getByRole('button', { name: '添加内容', exact: true }).click()
    await probe.getByText('上传附件', { exact: true }).click()
    const modal = probe.getByRole('dialog')
    await modal
      .locator('input[type="file"]')
      .setInputFiles('frontend/test/browser/fixtures/imgA.png')
    await probe.waitForFunction(() =>
      document.querySelector('.attachment-status-tag')?.textContent.includes('上传中')
    )
    check(uploadStarted, '没有经过真实上传请求')
    await probe
      .locator('.chat-header')
      .evaluate(
        (element, id) => element.__vueParentComponent.exposed.selectThreadFromRoute(id),
        ids[1]
      )
    await probe
      .locator('.chat-header .session-title')
      .getByText('附件会话 2', { exact: true })
      .waitFor()
    releaseUpload()
    await modal.getByText('已上传', { exact: true }).waitFor()
    await modal.getByRole('button', { name: '添加附件', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    check((await probe.locator('.attachment-file-card').count()) === 0, 'A 的附件进入了 B 的草稿')
    await probe
      .locator('.chat-header')
      .evaluate(
        (element, id) => element.__vueParentComponent.exposed.selectThreadFromRoute(id),
        ids[0]
      )
    await probe
      .locator('.chat-header .session-title')
      .getByText('附件会话 1', { exact: true })
      .waitFor()
    await probe.locator('.attachment-file-card').getByText('from-a.txt', { exact: true }).waitFor()
    await probe.screenshot({ path: '/tmp/yuxi-attachment-draft-owner.png' })
    await probe.getByRole('button', { name: '折叠侧边栏', exact: true }).click()
    await probe.evaluate(() => document.documentElement.classList.add('dark'))
    await probe.setViewportSize({ width: 390, height: 844 })
    await probe.waitForTimeout(350)
    await probe.screenshot({ path: '/tmp/yuxi-attachment-draft-owner-dark.png' })
    return { originalDraftRetained: true, otherDraftUntouched: true, delayedUpload: true }
  } finally {
    releaseUpload()
    await probe.close()
  }
}
