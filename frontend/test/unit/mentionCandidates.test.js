import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, nextTick, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

test('mention 的智能体与对话各显示前五个匹配项，对话排在智能体后且键盘顺序一致', async (t) => {
  const previousDocument = globalThis.document
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  let app
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    const sessions = [
      { thread_id: 'current', title: '当前对话' },
      ...Array.from({ length: 8 }, (_, i) => ({ thread_id: `session-${i}`, title: `对话 ${i}` }))
    ]
    const deviceRequests = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      const request = new URL(url, 'http://localhost')
      if (request.pathname === '/api/devices') {
        return new Promise((resolve) => deviceRequests.push({ resolve, signal: options.signal }))
      }
      assert.ok(['/api/mention/sessions', '/api/mention/search'].includes(request.pathname))
      const query = request.searchParams.get('query') || ''
      return Response.json(request.pathname === '/api/mention/sessions'
        ? sessions.filter((session) => session.title.includes(query))
        : [])
    })
    const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/MessageInputComponent.vue')
    configureApiAuth({ getToken: () => 'test-token' })
    globalThis.document = { addEventListener() {}, removeEventListener() {} }
    const renderer = createRenderer({
      createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null
    })
    let state
    const props = reactive({ modelValue: '', enableUploads: true, threadId: 'current', mention: {
        agents: Array.from({ length: 8 }, (_, i) => ({ agent_id: `agent-${i}`, name: `智能体 ${i}` })),
        knowledgeBases: [{ kb_id: 'kb', name: '知识库' }],
        mcps: [{ slug: 'mcp', name: 'MCP' }],
        plugins: [{ id: 'custom-image', name: '自定义图像', description: '绘制插图' }],
        skills: [{ slug: 'skill', name: 'Skill' }]
    } })
    app = renderer.createApp({ setup() {
      state = component.setup(props, { expose() {}, emit() {} })
      return () => null
    } })
    app.provide(ssrContextKey, {})
    app.mount({})
    t.mock.timers.enable({ apis: ['setTimeout'] })

    state.updateMentionItems('')
    assert.deepEqual(state.mentionItems.value.agents.map((item) => item.value),
      ['agent-0', 'agent-1', 'agent-2', 'agent-3', 'agent-4'])
    t.mock.timers.tick(250)
    await setImmediate()
    assert.equal(state.sessionSearchLoading.value, false)
    assert.equal(state.sessionSearchError.value, false)
    assert.deepEqual(state.mentionItems.value.sessions.map((item) => item.value),
      ['session-0', 'session-1', 'session-2', 'session-3', 'session-4'])
    assert.deepEqual(state.mentionSelectableItems.value.map((item) => item.type), [
      'action', 'action', 'knowledge', 'mcp', 'plugin', 'skill',
      'agent', 'agent', 'agent', 'agent', 'agent',
      'session', 'session', 'session', 'session', 'session'
    ])
    for (const group of ['knowledgeBases', 'mcps', 'plugins', 'skills', 'agents', 'sessions']) {
      for (const [index, item] of state.mentionItems.value[group].entries()) {
        state.mentionSelectedIndex.value = state.mentionSelectableItems.value.indexOf(item)
        assert.equal(state.isItemSelected(item), true)
        assert.equal(state.isItemSelected(state.mentionItems.value[group][index + 1]), false)
      }
    }

    state.mentionPopupVisible.value = true
    state.mentionSelectedIndex.value = 3
    state.handleMentionNavigation({ key: 'ArrowDown', preventDefault() {} })
    assert.equal(state.mentionSelectableItems.value[state.mentionSelectedIndex.value].type, 'plugin')
    assert.equal(state.isItemSelected(state.mentionItems.value.plugins[0]), true)
    state.handleMentionNavigation({ key: 'ArrowDown', preventDefault() {} })
    assert.equal(state.mentionSelectableItems.value[state.mentionSelectedIndex.value].type, 'skill')
    state.handleMentionNavigation({ key: 'ArrowUp', preventDefault() {} })
    assert.equal(state.mentionSelectableItems.value[state.mentionSelectedIndex.value].type, 'plugin')
    for (const query of ['图像', 'CUSTOM-IMAGE', '插图', 'plugin']) {
      state.updateMentionItems(query)
      assert.deepEqual(state.mentionItems.value.plugins.map((item) => item.value), ['custom-image'])
    }
    state.updateMentionItems('不可匹配')
    assert.deepEqual(state.mentionItems.value.plugins, [])

    state.mentionQuery.value = '7'
    state.updateMentionItems('7')
    assert.deepEqual(state.mentionItems.value.agents.map((item) => item.value), ['agent-7'])
    t.mock.timers.tick(250)
    await setImmediate()
    assert.equal(state.sessionSearchLoading.value, false)
    assert.deepEqual(state.mentionItems.value.sessions.map((item) => item.value), ['session-7'])

    state.mentionQuery.value = ''
    state.updateMentionItems('')
    state.mentionSelectedIndex.value = 4
    props.mention.plugins = []
    await nextTick()
    assert.deepEqual(state.mentionItems.value.plugins, [])
    assert.equal(state.mentionSelectedIndex.value, 0)
    props.mention.plugins = [{ id: 'abridge', name: '设备操作' }]
    await nextTick()
    assert.deepEqual(state.mentionItems.value.plugins.map((item) => item.value), ['abridge'])

    assert.equal(deviceRequests.length, 1)
    state.mentionPopupVisible.value = false
    state.mentionPopupVisible.value = true
    assert.equal(deviceRequests[0].signal.aborted, true)
    const selectedAgent = state.mentionItems.value.agents[0]
    state.mentionSelectedIndex.value = state.mentionSelectableItems.value.indexOf(selectedAgent)
    deviceRequests[1].resolve(Response.json({ state: 'ready', devices: [{ id: 'dev-a', name: '会议室' }] }))
    await setImmediate()
    deviceRequests[0].resolve(Response.json({ state: 'ready', devices: [{ id: 'old', name: '过期设备' }] }))
    await setImmediate()
    assert.equal(state.isItemSelected(selectedAgent), true)
    assert.deepEqual(state.matchingDevices.value.map((item) => item.value), ['dev-a'])
    state.mentionQuery.value = 'DEV-A'
    assert.deepEqual(state.matchingDevices.value.map((item) => item.insertValue), ['dev-a 会议室'])
    state.mentionQuery.value = '过期'
    assert.deepEqual(state.matchingDevices.value, [])
    assert.equal(deviceRequests.length, 2)
    state.mentionPopupVisible.value = false
    state.mentionPopupVisible.value = true
    deviceRequests[2].resolve(Response.json({ state: 'login_required', devices: [] }))
    await setImmediate()
    assert.deepEqual(state.matchingDevices.value, [])
    assert.match(state.deviceListHint.value, /设备管理.*授权/)
  } finally {
    app?.unmount()
    await server.close()
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
  }
})
