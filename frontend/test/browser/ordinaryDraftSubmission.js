// 普通 /agent 页面与公开协议 fixture；只在浏览器拦截请求，不写业务记录。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/ordinaryDraftSubmission.js
// prettier-ignore
async page => {
  await page.unrouteAll({ behavior: 'wait' })
  const check = (ok, message) => { if (!ok) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const imageUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII='
  const agent = { agent_id: 'draft-agent', name: '已授权草稿智能体', can_run: true, config_json: { context: { model: 'fixture:chat' } }, configurable_items: {}, metadata: {} }
  let phase = 'idle', releaseInput = null, managementKnowledgeReads = 0, accessibleKnowledgeReads = 0
  let discoveryMode = 'disabled'
  const submissions = []
  const thread = () => ({ id: 'draft-thread', object: 'agent.session', status: phase === 'idle' ? 'completed' : 'in_progress',
    agent: { id: agent.agent_id }, metadata: {}, created_at: 1793400000,
    yuxi: { title: '普通草稿验收', context: {}, config: {}, project_id: null,
      current_turn: phase === 'idle' ? null : { id: 'draft-turn', status: 'in_progress' } } })
  await page.route('https://api.github.com/**', route => route.fulfill({ json: {} }))
  await page.route('**/api/**', async route => {
    const path = route.request().url().split('?')[0].replace(origin, '')
    let json = { data: [], files: [], attachments: [], skills: [], projects: [], providers: [], config: {} }
    if (path === '/api/system/info') json = { success: true, data: { branding: { name: '语析', logo: '/favicon.svg' } } }
    if (path.endsWith('/discovery')) {
      if (discoveryMode === 'failed') return route.fulfill({ status: 503, json: { detail: 'fixture discovery unavailable' } })
      json = { capabilities: { features: { knowledge: discoveryMode === 'enabled' } } }
    }
    if (path.endsWith('/auth/me')) json = { id: 'draft-user', uid: 'draft-user', username: '草稿测试', role: 'superadmin' }
    if (path.endsWith('/check-first-run')) json = { first_run: false }
    if (path === '/api/system/config') json = { default_model: 'fixture:chat' }
    if (path === '/api/agent') json = { agents: [] }
    if (path === '/api/agent/draft-agent') json = agent
    if (path === '/api/projects') json = []
    if (path === '/api/knowledge/knowledge-bases') managementKnowledgeReads++
    if (path === '/api/knowledge/knowledge-bases/accessible') accessibleKnowledgeReads++
    if (path === '/api/v1/agents/sessions') json = { data: [thread()], has_more: false }
    if (path.endsWith('/draft-thread') || path.endsWith('/viewed')) json = thread()
    if (path.endsWith('/items')) json = { data: [], has_more: false, yuxi: { runs: [] } }
    if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    if (path.endsWith('/queue')) json = { inputs: [], status: 'ready', queue_paused: false }
    if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    if (path.endsWith('/feedback')) json = { items: [] }
    if (path.endsWith('/turns/draft-turn')) json = { id: 'draft-turn', status: 'in_progress', yuxi: {
      current_run_id: phase === 'missing-run' ? null : 'draft-run', cancellation_pending: phase === 'cancelling',
      waitpoint: phase === 'cooperation' ? { kind: 'cooperation' } : null } }
    if (path === '/api/agent/images') json = { image_url: imageUrl, thumbnail_url: imageUrl, mime_type: 'image/png', width: 1, height: 1, format: 'PNG', size_bytes: 68 }
    if (path.endsWith('/events')) {
      if (route.request().method() === 'POST') {
        submissions.push(route.request().postDataJSON())
        if (phase === 'pending') await new Promise(resolve => { releaseInput = resolve })
        return route.fulfill({ json: { input_id: 'draft-input', status: 'pending' } })
      }
      return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
    }
    if (path.endsWith('/inputs/draft-input')) json = { input_id: 'draft-input', status: 'pending' }
    return route.fulfill({ json })
  })
  await page.addInitScript(() => { localStorage.setItem('user_token', 'draft-fixture'); localStorage.setItem('theme', 'light') })
  await page.setViewportSize({ width: 1440, height: 960 })
  const load = async value => {
    phase = value
    await page.goto(origin + '/agent/draft-thread')
    await page.locator('.agent-session-workspace .mention-editor[contenteditable="true"]').waitFor()
    await editor.fill('')
    await page.waitForTimeout(300)
  }
  const editor = page.locator('.agent-session-workspace .mention-editor').first()
  const pasteImage = async () => {
    await editor.evaluate(node => {
      const data = new DataTransfer()
      data.items.add(new File(['image'], 'draft.png', { type: 'image/png' }))
      node.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
    })
    await page.getByRole('button', { name: '移除图片 draft.png', exact: true }).waitFor()
  }
  try {
    await load('idle')
    check(await page.locator('.agent-session-workspace').innerText().then(text => text.includes('当前智能体：' + agent.name)), '空目录不能抹去已授权智能体详情')
    await pasteImage()
    const send = page.getByRole('button', { name: '发送消息', exact: true })
    check(await send.isEnabled(), '纯图片草稿必须可以发送')
    await send.click()
    await page.waitForFunction(() => document.querySelector('.image-preview') === null)
    check(submissions.at(-1)?.events?.[0]?.yuxi?.mode === 'follow_up', '纯图片普通消息必须使用 FIFO')
    check(submissions.at(-1)?.events?.[0]?.input?.[0]?.content?.[0]?.type === 'input_image', '发送协议必须保留图片正文')

    await load('running')
    await page.getByRole('button', { name: '停止回答', exact: true }).waitFor()
    await pasteImage()
    check(await page.getByRole('button', { name: '停止回答', exact: true }).count() === 0, '运行中的图片草稿必须替换停止入口')
    check(await page.getByRole('button', { name: '发送消息', exact: true }).isEnabled(), '运行中图片草稿可准备发送')
    await page.getByRole('button', { name: '移除图片 draft.png', exact: true }).click()
    await page.getByRole('button', { name: '停止回答', exact: true }).waitFor()
    await page.screenshot({ path: '/tmp/yuxi-ordinary-draft-stop.png' })

    for (const mode of ['cooperation', 'cancelling', 'missing-run']) {
      await load(mode)
      await editor.fill('当前草稿')
      check(await page.getByRole('button', { name: '引导', exact: true }).count() === 0, mode + '不能提供引导')
      if (mode === 'cancelling') check(await page.getByRole('button', { name: '发送消息', exact: true }).isDisabled(), '取消清理期间不能发送')
    }

    await load('pending')
    await editor.fill('优先调整方向')
    await page.getByRole('button', { name: '引导', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('.mention-editor').textContent === '')
    await editor.fill('等待提交时的新草稿')
    check(await page.getByRole('button', { name: '引导', exact: true }).count() === 0, '接收结果待确认时不能重复引导')
    check(submissions.at(-1)?.events?.[0]?.yuxi?.mode === 'steer', '普通引导必须显式提交 steer')
    const accepted = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/events'))
    releaseInput()
    releaseInput = null
    await accepted
    check(managementKnowledgeReads === 0, 'Knowledge关闭时布局不应加载管理列表')
    await page.screenshot({ path: '/tmp/yuxi-ordinary-draft-pending.png' })
    discoveryMode = 'failed'
    await load('idle')
    check(managementKnowledgeReads === 0, 'discovery失败时布局不应加载管理列表')
    discoveryMode = 'enabled'
    await load('idle')
    check(managementKnowledgeReads === 1, 'Knowledge开启时布局应加载一次管理列表')
    return { imageOnlySend: true, imageWireContent: true, runningImageDraft: true, stopRestored: true,
      steerBusyGuards: true, ordinaryModes: ['follow_up', 'steer'], emptyDirectoryDetail: true,
      layoutKnowledgeGate: { disabled: 0, failed: 0, enabled: managementKnowledgeReads },
      existingAccessibleKnowledgeRequests: accessibleKnowledgeReads }
  } finally {
    releaseInput?.()
    await page.unrouteAll({ behavior: 'wait' })
  }
}
