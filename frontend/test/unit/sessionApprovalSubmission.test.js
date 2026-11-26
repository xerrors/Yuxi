import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { reactive, ref, toRaw } from 'vue'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, agentApi, useThreadTurnAction
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  ;({ agentApi } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ useThreadTurnAction } = await server.ssrLoadModule(
    '/src/modules/session/model/useThreadTurnAction.js'
  ))
})
after(async () => { await server?.close() })

/** 创建具有独立 Thread、Turn、Run 和等待点的审批。 */
function waitingState(threadId, suffix) {
  return {
    currentTurnId: `turn-${suffix}`,
    activeRunId: `run-${suffix}`,
    turnStatus: 'requires_action',
    runStateVersion: 1,
    agentStateRequestVersion: 0,
    approvalSubmitting: false,
    isStreaming: false,
    pendingInterrupt: {
      threadId,
      interruptedRunId: `run-${suffix}`,
      waitpointId: `wait-${suffix}`,
      kind: 'question',
      questions: [{ question_id: `q-${suffix}`, question: '请确认' }]
    }
  }
}

function setup(t) {
  const threadId = ref('root')
  const states = reactive({ root: waitingState('root', 'A'), child: waitingState('child', 'B') })
  const stream = [], history = [], stops = []
  const runtime = {
    peekThreadState: (id) => states[id],
    isCurrentThreadState: (id, candidate) => states[id] === candidate,
    observeThread: () => () => {},
    resetThreadExecution() {},
    reconcileTurnHistory: async (...args) => history.push(args),
    startRunStream: async (...args) => stream.push(args),
    requestTurnCancellation: async id => { stops.push(id); return false }
  }
  t.mock.method(agentApi, 'getThreadTurn', async (id) => {
    const state = states[id]
    return { id: state.currentTurnId, status: state.turnStatus, yuxi: {
      current_run_id: state.activeRunId,
      waitpoint: { id: state.pendingInterrupt.waitpointId, run_id: state.activeRunId,
        kind: 'answer', questions: state.pendingInterrupt.questions }
    } }
  })
  const controller = useThreadTurnAction({ threadId, enabled: ref(true), runtime })
  return { threadId, states, controller, runtime, stream, history, stops }
}

test('原等待点提交失败时保留问题，卡片显示错误且允许重试', async (t) => {
  const h = setup(t)
  let calls = 0
  t.mock.method(agentApi, 'resumeThreadTurn', async () => {
    if (++calls === 1) throw new Error('暂时无法提交')
    return { run_id: 'run-C' }
  })
  const pending = h.states.root.pendingInterrupt
  const answer = { 'q-A': '继续' }
  const submitting = h.controller.submit(answer, 'wait-A')
  assert.equal(h.controller.processing.value, true)
  assert.equal(await h.controller.submit(answer, 'wait-A'), false)
  assert.equal(await submitting, false)
  assert.equal(h.states.root.pendingInterrupt, pending)
  assert.equal(h.controller.visible.value, true)
  assert.equal(h.controller.processing.value, false)
  assert.equal(h.controller.error.value, '暂时无法提交')

  assert.equal(await h.controller.submit(answer, 'wait-A'), true)
  assert.equal(h.controller.visible.value, false)
  assert.deepEqual(h.history, [['root', 'turn-A']])
  assert.deepEqual(h.stream, [['root', 'run-C', null, { turnId: 'turn-A' }]])
})

test('切换到成员后拒绝根会话旧卡片发出的提交和停止操作', async (t) => {
  const h = setup(t)
  t.mock.method(agentApi, 'resumeThreadTurn', async () => assert.fail('旧卡片发出了提交'))
  h.threadId.value = 'child'
  assert.equal(h.controller.action.value.waitpointId, 'wait-B')
  assert.equal(await h.controller.submit({ 'q-A': '继续' }, 'wait-A'), false)
  assert.equal(await h.controller.cancel('wait-A'), false)
  assert.deepEqual(h.stops, [])
  assert.equal(h.states.child.pendingInterrupt.waitpointId, 'wait-B')
})

test('停止请求未确认时，当前等待点保留表单和失败反馈', async (t) => {
  const h = setup(t)
  const { promise, resolve } = Promise.withResolvers()
  t.mock.method(h.runtime, 'requestTurnCancellation', async () => {
    h.states.root.cancellationPending = true
    const outcome = await promise
    h.states.root.cancellationPending = false
    return outcome
  })
  const stopping = h.controller.cancel('wait-A')
  assert.equal(h.controller.visible.value, true)
  assert.equal(h.controller.processing.value, true)
  resolve(false)
  assert.equal(await stopping, false)
  assert.equal(h.controller.visible.value, true)
  assert.equal(h.controller.processing.value, false)
  assert.equal(h.controller.error.value, '停止请求未确认，请重试。')
})

for (const lateResult of ['rejected', 'accepted']) {
  test(`旧等待点响应${lateResult}时不改变已推进的新等待点或其他成员`, async (t) => {
    const h = setup(t)
    const { promise: held, resolve, reject } = Promise.withResolvers()
    const { promise: submitted, resolve: markSubmitted } = Promise.withResolvers()
    t.mock.method(agentApi, 'resumeThreadTurn', () => { markSubmitted(); return held })
    const submitting = h.controller.submit({ 'q-A': '继续' }, 'wait-A')
    await submitted
    Object.assign(h.states.root, waitingState('root', 'C'), { runStateVersion: 2 })
    h.threadId.value = 'child'
    const current = structuredClone(toRaw(h.states))
    if (lateResult === 'accepted') resolve({ run_id: 'run-late' })
    else reject(new Error('延迟失败'))
    await submitting
    assert.deepEqual(structuredClone(toRaw(h.states)), current)
    assert.deepEqual(h.stream, [])
    assert.equal(h.controller.error.value, '')
    assert.equal(h.controller.action.value.waitpointId, 'wait-B')
  })
}

test('审批已接收后续跑连接失败，不能恢复已消费的旧等待点', async (t) => {
  const h = setup(t)
  const { promise: streamFailed, resolve: recordFailure } = Promise.withResolvers()
  t.mock.method(message, 'error', recordFailure)
  t.mock.method(console, 'error', () => {})
  t.mock.method(agentApi, 'resumeThreadTurn', async () => ({ run_id: 'run-next' }))
  t.mock.method(h.runtime, 'startRunStream', async () => { throw Error('续跑连接失败') })
  assert.equal(await h.controller.submit({ 'q-A': '继续' }, 'wait-A'), true)
  assert.match(await streamFailed, /续跑连接失败/)
  assert.equal(h.states.root.pendingInterrupt, null)
  assert.equal(h.states.root.isStreaming, true)
  assert.equal(h.controller.visible.value, false)
  assert.equal(h.controller.processing.value, false)
})
