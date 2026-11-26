import assert from 'node:assert/strict'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { setImmediate } from 'node:timers'
import { fileURLToPath } from 'node:url'

import { createTestServer } from '../helpers/createTestServer.js'
import { createItemState } from '../../src/modules/session/model/agentItems.js'
import { useAgentThreadState } from '../../src/modules/session/model/useAgentThreadState.js'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
let server
let agentApi
let useAgentInputQueue

before(async () => {
  const storage = new Map()
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key)
  }
  server = await createTestServer({ root: webRoot, server: { middlewareMode: true } })
  ;({ agentApi } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ useAgentInputQueue } = await server.ssrLoadModule(
    '/src/modules/session/model/useAgentInputQueue.js'
  ))

})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

test('转引导重试复用幂等键，成功和冲突都回读队列且不写正式历史', async () => {
  const state = {
    queuedInputs: [{ input_id: 'F1', kind: 'follow_up', status: 'pending' }],
    inputQueueMonitor: null, itemState: { items: {} }, optimisticInputs: {}
  }
  const original = { ...agentApi }
  const { ErrorHandler } = await server.ssrLoadModule('/src/shared/lib/errorHandler.js')
  const originalHandleError = ErrorHandler.handleError
  ErrorHandler.handleError = () => {}
  const keys = []
  let reads = 0
  agentApi.getThreadInput = async () => ({ status: 'pending' })
  agentApi.getThreadQueue = async () => {
    reads++
    return { inputs: reads === 1 ? [{ input_id: 'F1', kind: 'steer', status: 'pending' }] : [] }
  }
  agentApi.promoteThreadInput = async (_thread, _input, key) => {
    keys.push(key)
    if (keys.length === 1) throw new Error('response lost')
    if (keys.length === 3) throw Object.assign(new Error('consumed'), { status: 409 })
  }
  const queue = useAgentInputQueue({ getThreadState: () => state })
  try {
    assert.equal(await queue.promoteInput('t', 'F1'), false)
    assert.equal(await queue.promoteInput('t', 'F1'), true)
    assert.equal(keys[0], keys[1])
    assert.equal(state.queuedInputs[0].kind, 'steer')
    assert.equal(await queue.promoteInput('t', 'F1'), false)
    assert.notEqual(keys[1], keys[2])
    assert.equal(reads, 2)
    assert.deepEqual(state.queuedInputs, [])
    assert.deepEqual(state.itemState.items, {})
  } finally {
    queue.stopAllInputMonitors('t')
    Object.assign(agentApi, original)
    ErrorHandler.handleError = originalHandleError
  }
})

test('较早队列快照不能覆盖新快照，取消一条不移除其他引导', async () => {
  const state = { queuedInputs: [], inputQueueMonitor: null, itemState: { items: {} }, optimisticInputs: {} }
  const original = { ...agentApi }
  let resolveOld
  agentApi.getThreadQueue = () => new Promise((resolve) => { resolveOld = resolve })
  agentApi.getThreadInput = async () => ({ status: 'pending' })
  agentApi.cancelThreadInput = async () => ({ status: 'accepted' })
  const queue = useAgentInputQueue({ getThreadState: () => state })
  try {
    const oldRead = queue.syncQueuedInputs('t')
    const rows = ['S1', 'S2'].map((input_id) => ({ input_id, kind: 'steer', status: 'pending' }))
    agentApi.getThreadQueue = async () => ({ inputs: rows })
    await queue.syncQueuedInputs('t')
    resolveOld({ inputs: [{ input_id: 'F1', kind: 'follow_up', status: 'pending' }] })
    await oldRead
    assert.deepEqual(state.queuedInputs.map((item) => item.input_id), ['S1', 'S2'])
    agentApi.getThreadQueue = async () => ({ inputs: [rows[1]] })
    assert.equal(await queue.cancelInput('t', 'S1'), true)
    assert.deepEqual(state.queuedInputs.map((item) => item.input_id), ['S2'])
    assert.deepEqual(state.itemState.items, {})
  } finally {
    queue.stopAllInputMonitors('t')
    Object.assign(agentApi, original)
  }
})

test('完整队列快照保留独立 steer 节点并遵循服务器优先顺序', async () => {
  const state = {
    queuedInputs: [
      { input_id: 'F1', kind: 'follow_up', status: 'pending', content: '普通消息' },
      { input_id: 'S1', kind: 'steer', status: 'pending', content: '旧内容' },
      { input_id: 'S1', kind: 'steer', status: 'pending', content: '重复投影' },
      { input_id: 'local', status: 'sending' }
    ],
    inputQueueMonitor: null,
    itemState: { items: {} }, optimisticInputs: {}
  }
  const originalQueue = agentApi.getThreadQueue
  const originalInput = agentApi.getThreadInput
  const queue = useAgentInputQueue({ getThreadState: () => state })
  agentApi.getThreadInput = async () => ({ status: 'pending' })
  agentApi.getThreadQueue = async () => ({ inputs: [
    { input_id: 'S1', kind: 'steer', status: 'pending', content: 'S1' },
    { input_id: 'S2', kind: 'steer', status: 'pending', content: 'S2' },
    { input_id: 'F1', kind: 'follow_up', status: 'pending', content: '普通消息' }
  ] })
  try {
    await queue.syncQueuedInputs('thread-1')
    assert.deepEqual(state.queuedInputs.map((input) => input.input_id), ['S1', 'S2', 'F1', 'local'])
    assert.equal(state.queuedInputs[0].content, 'S1')
    assert.deepEqual(state.itemState.items, {})
    assert.ok(state.inputQueueMonitor)
    assert.deepEqual([...state.inputQueueMonitor.inputIds], ['S1', 'S2', 'F1'])
    agentApi.getThreadQueue = async () => ({ inputs: [
      { input_id: 'F1', kind: 'follow_up', status: 'pending', content: '普通消息' }
    ] })
    await queue.syncQueuedInputs('thread-1')
    assert.deepEqual(state.queuedInputs.map((input) => input.input_id), ['F1', 'local'])
  } finally {
    queue.stopAllInputMonitors('thread-1')
    agentApi.getThreadQueue = originalQueue
    agentApi.getThreadInput = originalInput
  }
})

test('尚未接收的本地输入不能被取消或订阅', async () => {
  const state = {
    queuedInputs: [{ input_id: 'local-key', status: 'sending' }],
    inputQueueMonitor: null,
    itemState: { items: {} }, optimisticInputs: {}
  }
  const originalQueue = agentApi.getThreadQueue
  const originalInput = agentApi.getThreadInput
  const originalCancel = agentApi.cancelThreadInput
  const calls = []
  agentApi.getThreadQueue = async () => ({ inputs: [] })
  agentApi.getThreadInput = async () => { calls.push('input'); throw new Error('not persisted') }
  agentApi.cancelThreadInput = async () => { calls.push('cancel'); throw new Error('not persisted') }
  try {
    const queue = useAgentInputQueue({ getThreadState: () => state })
    await queue.resumeQueuedInputs('thread-1')
    assert.equal(await queue.cancelInput('thread-1', 'local-key'), false)
    assert.deepEqual(state.queuedInputs, [{ input_id: 'local-key', status: 'sending' }])
    assert.deepEqual(calls, [])
  } finally {
    agentApi.getThreadQueue = originalQueue
    agentApi.getThreadInput = originalInput
    agentApi.cancelThreadInput = originalCancel
  }
})

test('重置执行观察保留唯一 item 事实和排队 Input 的监视', () => {
  const chatState = { threadStates: {} }
  const { getThreadState, resetThreadExecution } = useAgentThreadState({
    chatState
  })
  const state = getThreadState('thread-1')
  const controller = new AbortController()
  let runAborted = false
  state.runStreamAbortController = { abort: () => (runAborted = true) }
  state.inputQueueMonitor = { controller, timer: null, inputIds: new Set(['input-2']) }
  state.itemState.items['input-1'] = { type: 'message', content: [{ type: 'output_text', text: '第一轮回复' }] }

  resetThreadExecution('thread-1', { preserveInputMonitors: true })

  assert.equal(runAborted, true)
  assert.equal(controller.signal.aborted, false)
  assert.deepEqual([...state.inputQueueMonitor.inputIds], ['input-2'])
  assert.equal(state.itemState.items['input-1'].content[0].text, '第一轮回复')
})

for (const action of ['reset', 'cleanup']) {
  test(`${action} 停止 Input 监视与重连计时器`, (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const chatState = { threadStates: {} }
    const { getThreadState, resetThreadExecution, cleanupThreadState } = useAgentThreadState({
      chatState
    })
    const state = getThreadState('thread-1')
    const controller = new AbortController()
    let retried = false
    state.inputQueueMonitor = {
      inputIds: new Set(['input-1']),
      controller,
      timer: setTimeout(() => { retried = true }, 20)
    }

    if (action === 'reset') resetThreadExecution('thread-1')
    else cleanupThreadState('thread-1')

    t.mock.timers.tick(40)
    assert.equal(controller.signal.aborted, true)
    assert.equal(retried, false)
    assert.equal(state.inputQueueMonitor, null)
  })
}

test('FIFO 领取只用真实 item 替换对应乐观输入，并通知持久运行恢复', async (t) => {
  const state = {
    queuedInputs: [],
    inputQueueMonitor: null,
    itemState: createItemState(),
    optimisticInputs: { input: { id: 'local' } }
  }
  const item = {
    id: 'input_1',
    type: 'message',
    role: 'user',
    status: 'completed',
    content: [],
    yuxi: { input_id: 'input' }
  }
  t.mock.method(agentApi, 'getThreadQueue', async () => ({
    inputs: [{ input_id: 'input', status: 'pending' }]
  }))
  t.mock.method(agentApi, 'getThreadInput', async () => ({
    status: 'consumed',
    run_id: 'run',
    turn_id: 'turn',
    items: [item]
  }))
  const consumed = []
  const queue = useAgentInputQueue({
    getThreadState: () => state,
    onInputConsumed: (...args) => consumed.push(args)
  })
  t.after(() => queue.stopAllInputMonitors('thread'))
  await queue.syncQueuedInputs('thread')
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(state.itemState.items.input_1, item)
  assert.deepEqual(state.optimisticInputs, {})
  assert.deepEqual(consumed, [['thread']])
})

for (const failed of [false, true]) {
  test(`旧运行恢复${failed ? '失败' : '完成'}后，新 Input 观察仍能读取领取结果`, async (t) => {
    const { ErrorHandler } = await server.ssrLoadModule('/src/shared/lib/errorHandler.js')
    t.mock.method(ErrorHandler, 'handleError', () => {})
    const { getThreadState, resetThreadExecution } = useAgentThreadState({
      chatState: { threadStates: {} }
    })
    const { promise: recovering, resolve: notifyRecovering } = Promise.withResolvers()
    const { promise: oldRecovery, resolve: resolveOld, reject: rejectOld } = Promise.withResolvers()
    const { promise: newInput, resolve: resolveNewInput } = Promise.withResolvers()
    const { promise: newRecovered, resolve: notifyNewRecovered } = Promise.withResolvers()
    const errors = []
    const queue = useAgentInputQueue({
      getThreadState,
      onInputConsumed: () => {
        if (!getThreadState('thread').itemState.items['new-item']) {
          notifyRecovering()
          return oldRecovery
        }
        notifyNewRecovered()
      },
      onStreamError: (...args) => errors.push(args)
    })
    t.after(() => queue.stopAllInputMonitors('thread'))
    t.mock.method(agentApi, 'getThreadInput', async (_thread, inputId) => {
      if (inputId === 'old-input') return { status: 'consumed', run_id: 'old-run' }
      return newInput
    })

    queue.startInputMonitor('thread', 'old-input')
    await recovering
    resetThreadExecution('thread')
    queue.startInputMonitor('thread', 'new-input')
    if (failed) rejectOld(Object.assign(new Error('旧恢复响应已失效'), { status: 404 }))
    else resolveOld()
    await new Promise((resolve) => setImmediate(resolve))
    assert.deepEqual(errors, [])
    assert.deepEqual([...(getThreadState('thread').inputQueueMonitor?.inputIds || [])], ['new-input'])

    resolveNewInput({
      status: 'consumed',
      run_id: 'new-run',
      items: [{
        id: 'new-item', type: 'message', role: 'user', status: 'completed', content: [],
        yuxi: { input_id: 'new-input' }
      }]
    })
    await newRecovered
    assert.equal(getThreadState('thread').itemState.items['new-item'].yuxi.input_id, 'new-input')
  })
}
