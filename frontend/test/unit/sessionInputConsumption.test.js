import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

let server, api, useRuntime, ErrorHandler
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  ;({ agentApi: api } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ ErrorHandler } = await server.ssrLoadModule('/src/shared/lib/errorHandler.js'))
  ;({ useSessionRuntimeStore: useRuntime } = await server.ssrLoadModule(
    '/src/modules/session/model/sessionRuntime.js'
  ))
})
after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

/** 显式控制 HTTP 返回顺序，不依赖真实计时器。 */
function deferred() {
  let resolve
  const promise = new Promise((yes) => { resolve = yes })
  return { promise, resolve }
}
const settle = async () => {
  for (let i = 0; i < 8; i++) await setImmediate()
}
const item = (inputId, turnId, runId, role = 'user') => ({
  id: `item-${inputId}`, type: 'message', role, status: 'completed', turn_id: turnId,
  content: [{ type: role === 'user' ? 'input_text' : 'output_text', text: inputId }],
  yuxi: { input_id: inputId, message_id: inputId, run_id: runId }
})
const consumed = (inputId, turnId, runId) => ({
  input_id: inputId, status: 'consumed', turn_id: turnId, run_id: runId,
  items: [item(inputId, turnId, runId)]
})

/** 真实共享 runtime 消费协议响应，独立持久快照决定当前执行。 */
function setup(t) {
  const saved = { ...api }
  t.mock.method(ErrorHandler, 'handleError', () => {})
  const pinia = createPinia()
  setActivePinia(pinia)
  const current = { turnId: 'new-turn', runId: 'new-run', status: 'in_progress', output: [] }
  const subscriptions = []
  Object.assign(api, {
    getPublicThread: async () => ({
      id: 'child', yuxi: { current_turn: { id: current.turnId, status: current.status } }
    }),
    getThreadTurn: async (_threadId, id) => ({
      id, status: current.status,
      yuxi: { current_run_id: current.runId, runs: [], output: current.output }
    }),
    getSessionItems: async () => ({ data: current.output, has_more: false, yuxi: { runs: [] } }),
    getThreadQueue: async () => ({ inputs: [], status: 'ready', queue_paused: false }),
    getAgentState: async () => ({ agent_state: {}, interrupt: null }),
    streamThreadEvents: async (_thread, _cursor, { signal }) => {
      return new Response(new ReadableStream({
        start(controller) {
          subscriptions.push({ signal, controller })
          signal.addEventListener('abort', () => controller.close(), { once: true })
        }
      }))
    }
  })
  const runtime = useRuntime()
  const detach = runtime.observeThread('child', {})
  t.after(() => {
    detach()
    disposePinia(pinia)
    Object.assign(api, saved)
  })
  const summary = () => [{ session_id: 'root' }, {
    session_id: 'child', turn_id: current.turnId, current_run_id: current.runId,
    turn_status: current.status, run_status: current.status
  }]
  return { runtime, current, subscriptions, summary, detach }
}

test('旧 Input 的迟到消费事实不切回旧 Turn，也不终止新 SSE；相同摘要保持正确执行', async (t) => {
  const h = setup(t)
  const delayed = deferred()
  api.getThreadInput = () => delayed.promise
  const state = h.runtime.getThreadState('child')
  state.queuedInputs = [{ input_id: 'old-input', status: 'pending' }]
  state.optimisticInputs['old-input'] = [{ id: 'local-old' }]
  state.optimisticInputs['new-input'] = [{ id: 'local-new' }]
  state.pendingInputId = 'new-input'
  h.runtime.startInputMonitor('child', 'old-input')
  await h.runtime.reconcileCooperationSessions(h.summary())
  await settle()
  assert.equal(state.currentTurnId, 'new-turn')
  const signal = h.subscriptions[0].signal

  delayed.resolve(consumed('old-input', 'old-turn', 'old-run'))
  await settle()
  await h.runtime.reconcileCooperationSessions(h.summary())
  await settle()

  assert.equal(state.currentTurnId, 'new-turn')
  assert.equal(state.activeRunId, 'new-run')
  assert.equal(signal.aborted, false, '旧 Input 关联只属于该消息，不能替换当前订阅')
  assert.equal(h.subscriptions.length, 1)
  assert.equal(state.queuedInputs.length, 0)
  assert.equal(state.optimisticInputs['old-input'], undefined)
  assert.ok(state.optimisticInputs['new-input'], '消费一条不能清除另一条乐观输入')
  assert.equal(state.pendingInputId, 'new-input')
  const message = h.runtime.threadMessages.child.find((message) => message.input_id === 'old-input')
  assert.equal(message.turn_id, 'old-turn')
  assert.equal(message.run_id, 'old-run', '历史与调试保留该 Input 自己的执行归属')
  const output = item('live-result', 'new-turn', 'new-run', 'assistant')
  h.subscriptions[0].controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({
    type: 'agent.session.turn.item.done', event_id: 'current-output', session_id: 'child',
    turn_id: 'new-turn', item: output, yuxi: { run_id: 'new-run' }
  })}\n\n`))
  await settle()
  assert.ok(h.runtime.threadMessages.child.some((message) => message.content === 'live-result'),
    '迟到消费之后仍须接收当前执行的后续输出')
})

test('当前 Input 消费通过持久 current Turn 启动新轮次，并可推进后续 FIFO 输入', async (t) => {
  const h = setup(t)
  api.getThreadInput = async (_threadId, inputId) => consumed(inputId, h.current.turnId, h.current.runId)
  h.runtime.startInputMonitor('child', 'first-input')
  await settle()
  const state = h.runtime.getThreadState('child')
  assert.equal(state.currentTurnId, 'new-turn')
  assert.equal(state.activeRunId, 'new-run')
  Object.assign(h.current, { turnId: 'following-turn', runId: 'following-run' })
  h.runtime.startInputMonitor('child', 'following-input')
  await settle()
  assert.equal(state.currentTurnId, 'following-turn')
  assert.equal(state.activeRunId, 'following-run')
  assert.equal(h.subscriptions[0].signal.aborted, true)
  assert.equal(h.subscriptions[1].signal.aborted, false)
  assert.deepEqual(h.runtime.threadMessages.child.map((message) => message.input_id), ['first-input', 'following-input'])
})

test('同 Turn 已 steer 或恢复到新 Run 后，旧消费 Run 不使执行倒退且沿用唯一 SSE', async (t) => {
  const h = setup(t)
  await h.runtime.resumeActiveRunForThread('child')
  await settle()
  h.current.runId = 'resumed-run'
  await h.runtime.resumeActiveRunForThread('child')
  api.getThreadInput = async () => consumed('steer-input', 'new-turn', 'new-run')
  h.runtime.startInputMonitor('child', 'steer-input')
  await settle()
  assert.equal(h.runtime.getThreadState('child').activeRunId, 'resumed-run')
  assert.equal(h.subscriptions.length, 1)
  assert.equal(h.subscriptions[0].signal.aborted, false)
  assert.equal(h.runtime.threadMessages.child[0].run_id, 'new-run')
})

test('观察到消费之前已结束的轮次读取最终结果，不创建过期 SSE 或错误停止目标', async (t) => {
  const h = setup(t)
  Object.assign(h.current, {
    status: 'completed', output: [item('final-result', 'new-turn', 'new-run', 'assistant')]
  })
  api.getThreadInput = async () => consumed('finished-input', 'new-turn', 'new-run')
  h.runtime.startInputMonitor('child', 'finished-input')
  await settle()
  const state = h.runtime.getThreadState('child')
  assert.equal(state.activeRunId, null)
  assert.equal(state.currentTurnId, null)
  assert.equal(state.isStreaming, false)
  assert.equal(state.turnStatus, 'completed')
  assert.equal(h.subscriptions.length, 0)
  assert.ok(h.runtime.threadMessages.child.some((message) => message.content === 'final-result'))
})

test('消费之后的恢复等较早在途详情结束，再读取当前持久 Turn', async (t) => {
  const h = setup(t)
  const old = deferred()
  const getCurrent = api.getPublicThread
  let first = true
  api.getPublicThread = () => first ? (first = false, old.promise) : getCurrent()
  const restoring = h.runtime.resumeActiveRunForThread('child')
  await settle()
  api.getThreadInput = async () => consumed('latest-input', 'new-turn', 'new-run')
  h.runtime.startInputMonitor('child', 'latest-input')
  await settle()
  old.resolve({ id: 'child', yuxi: { current_turn: null } })
  await restoring
  await settle()
  assert.equal(h.runtime.getThreadState('child').currentTurnId, 'new-turn')
  assert.equal(h.runtime.getThreadState('child').activeRunId, 'new-run')
  assert.equal(h.subscriptions.length, 1)
})

test('消费后持久恢复暂时失败时保留观察，重试后仍能启动真实当前执行', async (t) => {
  const h = setup(t)
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const getCurrent = api.getPublicThread
  let reads = 0
  api.getPublicThread = () => {
    if (++reads === 1) throw new Error('temporary connection failure')
    return getCurrent()
  }
  api.getThreadInput = async () => consumed('retry-input', 'old-turn', 'old-run')
  h.runtime.startInputMonitor('child', 'retry-input')
  await settle()
  const state = h.runtime.getThreadState('child')
  assert.equal(state.activeRunId, null)
  assert.ok(state.inputQueueMonitor.inputIds.has('retry-input'))
  assert.equal(h.runtime.threadMessages.child[0].run_id, 'old-run')
  t.mock.timers.tick(5000)
  await settle()
  assert.equal(state.currentTurnId, 'new-turn')
  assert.equal(state.activeRunId, 'new-run')
  assert.equal(state.inputQueueMonitor, null)
  assert.equal(h.subscriptions.length, 1)
})

test('提交期间最后阅读视图隐藏仍收敛回执，结束后释放运行状态', async (t) => {
  const h = setup(t)
  const { createSessionInput } = await server.ssrLoadModule('/src/modules/session/model/sessionInput.js')
  const pending = deferred()
  api.sendThreadMessage = () => pending.promise
  api.getThreadInput = async () => ({ status: 'cancelled' })
  const accepted = []
  const input = createSessionInput({ runtime: h.runtime, onAccepted: (id) => accepted.push(id) })
  const state = h.runtime.getThreadState('child')
  const result = input.send('child', { text: '待接收内容', images: [], attachments: [], mode: 'follow_up' })
  h.detach()
  assert.equal(h.runtime.isCurrentThreadState('child', state), true, '在途提交独立持有状态')
  pending.resolve({ input_id: 'persisted-input' })
  assert.equal(await result, true)
  assert.deepEqual(accepted, ['child'])
  assert.equal(h.runtime.pendingSends.child, undefined)
  assert.equal(h.runtime.isCurrentThreadState('child', state), false, '回执收敛后不保留后台观察')
})

for (const outcome of ['accepted', 'rejected']) {
  test(`身份切换后的迟到${outcome}不消费新身份草稿、覆盖pending或重复提交`, async (t) => {
    const h = setup(t)
    const { createSessionInput } = await server.ssrLoadModule('/src/modules/session/model/sessionInput.js')
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/index.js')
    let settleResponse
    const durableInputs = []
    api.sendThreadMessage = () => new Promise((resolve, reject) => {
      settleResponse = () => {
        if (outcome === 'accepted') { durableInputs.push('old-input'); resolve({ input_id: 'old-input' }) }
        else reject(Object.assign(new Error('rejected'), { status: 403 }))
      }
    })
    const callbacks = []
    const input = createSessionInput({ runtime: h.runtime,
      onAccepted: () => callbacks.push('accepted'), onRejected: () => callbacks.push('rejected') })
    const result = input.send('child', { text: '旧身份输入', images: [], attachments: [], mode: 'follow_up' })
    useUserStore().token = 'new-identity'
    const release = h.runtime.observeThread('child', {})
    t.after(release)
    const current = h.runtime.getThreadState('child')
    current.pendingInputId = 'new-input'
    h.runtime.pendingSends.child = { text: '新身份草稿', data: { idempotency_key: 'new-key' }, submitting: true }
    settleResponse()
    assert.equal(await result, false)
    assert.deepEqual(durableInputs, outcome === 'accepted' ? ['old-input'] : [])
    assert.equal(h.runtime.pendingSends.child.text, '新身份草稿')
    assert.equal(h.runtime.pendingSends.child.submitting, true)
    assert.equal(current.pendingInputId, 'new-input')
    assert.deepEqual(callbacks, [])
  })
}
