import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryHistory, createRouter } from 'vue-router'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'

/** 装配普通会话的可执行状态，草稿与持久运行仍使用真实工作区逻辑。 */
async function readyWorkspace(t, options = {}) {
  const mounted = await mountSessionWorkspace(t, undefined, {
    threadId: 'thread', stubChildren: true,
    ...options, props: { agentId: 'agent', isNewSession: false, ...options.props }
  })
  t.mock.method(mounted.agentStore, 'fetchAgentDetail', async () => ({ can_run: true }))
  await mounted.state.refreshSessionAgent('thread', 'agent')
  return { ...mounted, thread: mounted.runtime.getThreadState('thread') }
}

test('普通会话仅有图片时可以发送，运行中图片草稿替换停止入口，移除后恢复停止', async (t) => {
  const sent = []
  const { state, thread } = await readyWorkspace(t, {
    send: async (id, draft) => { sent.push({ id, draft }); return true }
  })
  state.agentInputAreaRef.value = { hasImages: true }
  assert.equal(state.isSendButtonDisabled.value, false, '纯图片也是可发送草稿')
  assert.equal(state.shouldShowStopButton.value, false)
  Object.assign(thread, { isStreaming: true, activeRunId: 'run', activeRunSteerable: true })
  assert.equal(state.shouldShowStopButton.value, false, '有图片时按钮继续表示发送')
  assert.equal(state.canSubmitSteer.value, true)
  state.agentInputAreaRef.value.hasImages = false
  assert.equal(state.shouldShowStopButton.value, true, '移除最后一张图片恢复停止')
  assert.equal(state.canSubmitSteer.value, false)
  const image = { mimeType: 'image/png', base64: 'aW1hZ2U=' }
  await state.handleSendOrStop({ images: [image] })
  assert.equal(sent[0].id, 'thread')
  assert.deepEqual(sent[0].draft.images, [image])
  assert.equal(sent[0].draft.mode, 'follow_up', '普通图片消息保留 FIFO 语义')
})

test('普通引导和发送按钮共同遵守忙碌条件，解除阻塞后才提交 steer', async (t) => {
  const { state, thread, props } = await readyWorkspace(t)
  const submissions = []
  state.agentInputAreaRef.value = { submit: mode => submissions.push(mode) }
  state.userInput.value = '调整执行方向'
  Object.assign(thread, { isStreaming: true, activeRunId: 'run', activeRunSteerable: true })
  const blockedCases = [
    ['发送被调用者禁用', () => { props.sendDisabled = true }, () => { props.sendDisabled = false }],
    ['读取历史中', () => { state.isLoadingMessages.value = true }, () => { state.isLoadingMessages.value = false }],
    ['提交待确认', () => { state.pendingSends.value.thread = { submitting: true } }, () => { delete state.pendingSends.value.thread }],
    ['取消清理中', () => { thread.cancellationPending = true }, () => { thread.cancellationPending = false }],
    ['等待人工操作', () => { thread.turnStatus = 'requires_action' }, () => { thread.turnStatus = 'running' }],
    ['等待协作', () => { thread.cooperationWaiting = true }, () => { thread.cooperationWaiting = false }],
    ['缺少运行归属', () => { thread.activeRunId = null }, () => { thread.activeRunId = 'run' }],
    ['当前运行不可引导', () => { thread.activeRunSteerable = false }, () => { thread.activeRunSteerable = true }],
    ['发送冷却中', () => { state.sendCooldownActive.value = true }, () => { state.sendCooldownActive.value = false }]
  ]
  for (const [label, block, restore] of blockedCases) {
    block()
    assert.equal(state.canSubmitSteer.value, false, label)
    await state.handleDirectSteer()
    assert.deepEqual(submissions, [], label + '不能提交引导')
    restore()
  }
  assert.equal(state.canSubmitSteer.value, true)
  await state.handleDirectSteer()
  assert.deepEqual(submissions, ['steer'])
})

test('调用者禁用发送时，普通 follow-up 和 steer 都不能绕过提交边界', async (t) => {
  const sent = []
  const { state, thread } = await readyWorkspace(t, {
    props: { sendDisabled: true }, send: async (id, draft) => { sent.push({ id, draft }); return true }
  })
  Object.assign(thread, { isStreaming: true, activeRunId: 'run', activeRunSteerable: true })
  state.userInput.value = '准备中的草稿'
  state.agentInputAreaRef.value = { hasImages: true }
  assert.equal(state.isSendButtonDisabled.value, true)
  for (const mode of ['follow_up', 'steer']) {
    assert.equal(await state.handleSendMessage({ mode, images: [{ imageUrl: 'data:image/png;base64,aW1hZ2U=' }] }), false)
  }
  assert.deepEqual(sent, [])
  assert.equal(state.userInput.value, '准备中的草稿')
})

test('普通会话在目录为空时采用已授权详情的名称、模型与配置', async (t) => {
  const { state, agentStore } = await readyWorkspace(t)
  agentStore.agents = []
  agentStore.agentDetails.agent = {
    agent_id: 'agent', name: '已授权智能体', can_run: true,
    config_json: { context: { model: 'provider:detail', system_prompt: '详情配置' } },
    configurable_items: {}
  }
  state.currentThread.value = { id: 'thread', agent: { id: 'agent' }, yuxi: {} }
  assert.equal(state.currentAgentName.value, '已授权智能体')
  assert.equal(state.currentModelSpec.value, 'provider:detail')
  assert.equal(state.agentConfig.value.system_prompt, '详情配置')
})

/** 延迟导航读取，使用真实路由观察 member_session_id 协议。 */
async function routeWorkspace(t) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/agent/:thread_id', name: 'AgentCompWithThreadId', component: { render: () => null } },
    { path: '/other', component: { render: () => null } }
  ] })
  await router.push('/agent/thread?member_session_id=first')
  const mounted = await readyWorkspace(t, { router })
  const pending = new Map()
  t.mock.method(mounted.agentApi, 'getSessionNavigation', id => new Promise((resolve, reject) => pending.set(id, { resolve, reject })))
  return { ...mounted, router, pending }
}

const member = id => ({ session_id: id, name: id, tree_root_session_id: 'thread', parent_session_id: 'thread' })

test('普通 member 路由恢复只打开最新目标，旧错误不覆盖侧栏且不重写路由', async (t) => {
  const { state, router, pending } = await routeWorkspace(t)
  const route = router.currentRoute.value
  const old = state.openSessionFromRoute('first')
  const latest = state.openSessionFromRoute('latest')
  pending.get('latest').resolve(member('latest'))
  await latest
  pending.get('first').reject(new Error('过期读取失败'))
  await old
  assert.equal(state.agentPanelActiveSectionKey.value, 'session:latest')
  assert.equal(state.isFilePanelOpen.value, true)
  assert.equal(router.currentRoute.value, route, '恢复沿用当前 member_session_id 路由')
})

test('关闭侧栏、清除成员路由或离页后，迟到恢复不能重开成员', async (t) => {
  const { state, router, pending } = await routeWorkspace(t)
  for (const action of ['close', 'clear', 'leave']) {
    const request = state.openSessionFromRoute(action)
    if (action === 'close') state.closeFilePanel()
    if (action === 'clear') await state.openSessionFromRoute(null)
    if (action === 'leave') await router.push('/other')
    pending.get(action).resolve(member(action))
    await request
    assert.equal(state.isFilePanelOpen.value, false, action)
    assert.equal(state.agentPanelSections.value.some(section => section.sessionId === action), false, action)
  }
})

test('普通路由恢复拒绝外根或根本身，当前读取错误交回路由协调器', async (t) => {
  const { state, pending } = await routeWorkspace(t)
  for (const invalid of [
    { ...member('external'), tree_root_session_id: 'other' },
    { ...member('thread'), parent_session_id: null }
  ]) {
    const request = state.openSessionFromRoute(invalid.session_id)
    pending.get(invalid.session_id).resolve(invalid)
    await assert.rejects(request, /会话不属于当前主页面/)
    assert.equal(state.isFilePanelOpen.value, false)
  }
  const request = state.openSessionFromRoute('unavailable')
  pending.get('unavailable').reject(new Error('当前读取失败'))
  await assert.rejects(request, /当前读取失败/)
})
