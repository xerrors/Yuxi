import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { setImmediate } from 'node:timers'
import { createTestServer } from '../helpers/createTestServer.js'
import { createItemState } from '../../src/modules/session/model/agentItems.js'

let server, api, useAgentRunStream, processRunSseResponse, ErrorHandler
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  ;({ agentApi: api } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ useAgentRunStream, processRunSseResponse } = await server.ssrLoadModule(
    '/src/modules/session/model/useAgentRunStream.js'
  ))
  ;({ ErrorHandler } = await server.ssrLoadModule('/src/shared/lib/errorHandler.js'))
})
after(async () => {
  await server?.close()
  delete globalThis.localStorage
})
const tick = () => new Promise((resolve) => setImmediate(resolve))

test('停止请求立即展示等待，重复点击不重发，持久终态到达后结束', async (t) => {
  let accept
  const accepted = new Promise((resolve) => {
    accept = resolve
  })
  let requests = 0
  t.mock.method(api, 'cancelThreadTurn', async () => {
    requests++
    await accepted
  })
  const h = harness(t, [event('agent.session.turn.cancelled')])
  h.state.runStreamAbortController = new AbortController()
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: { current_run_id: 'run', cancellation_pending: true }
  }))
  const stopping = h.stream.requestTurnCancellation('thread')
  assert.equal(h.state.cancellationPending, true)
  assert.equal(h.state.activeRunSteerable, false)
  assert.equal(await h.stream.requestTurnCancellation('thread'), false)
  assert.equal(requests, 1)
  accept()
  assert.equal(await stopping, true)
  assert.equal(h.state.cancellationPending, true, '接收回执不能冒充取消完成')
  h.stream.stopRunStreamSubscription('thread')
  await h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  assert.equal(h.state.cancellationPending, false)
  assert.equal(h.state.isStreaming, false)
  assert.equal(h.state.turnStatus, 'cancelled')
})

test('其他页面发出的取消回执也展示正在停止', async (t) => {
  const h = harness(t, [
    {
      type: 'yuxi.session.input.accepted',
      session_id: 'thread',
      yuxi: { input_type: 'agent.session.input.cancel', turn_id: 'turn', run_id: 'run' }
    }
  ])
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: { current_run_id: 'run', cancellation_pending: true }
  }))
  await h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  assert.equal(h.state.cancellationPending, true)
  assert.equal(h.state.isStreaming, true)
})

test('人工等待取消的 interrupted Run 也能从快照恢复正在停止', async (t) => {
  const h = harness(t, [])
  t.mock.method(api, 'getPublicThread', async () => ({ yuxi: { current_turn: { id: 'turn' } } }))
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: {
      current_run_id: 'run',
      cancellation_pending: true,
      waitpoint: { kind: 'answer' },
      runs: [{ id: 'run', status: 'interrupted' }]
    }
  }))
  await h.stream.resumeActiveRunForThread('thread')
  await tick()
  assert.equal(h.state.cancellationPending, true)
  assert.equal(h.state.activeRunSteerable, false)
})

test('取消请求期间的旧等待快照不能恢复输入或显示审批', async (t) => {
  const h = harness(t, [event('yuxi.session.turn.waiting')])
  let accept
  t.mock.method(
    api,
    'cancelThreadTurn',
    () =>
      new Promise((resolve) => {
        accept = resolve
      })
  )
  t.mock.method(api, 'getPublicThread', async () => ({ yuxi: { current_turn: { id: 'turn' } } }))
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'requires_action',
    yuxi: { current_run_id: 'run', cancellation_pending: false }
  }))
  const stopping = h.stream.requestTurnCancellation('thread')
  await h.stream.resumeActiveRunForThread('thread')
  await tick()
  assert.equal(h.state.cancellationPending, true)
  assert.equal(h.state.activeRunSteerable, false)
  accept()
  await stopping
})

test('下一轮先被队列观察到时不继承上一轮的停止状态', async (t) => {
  const h = harness(t, [])
  h.state.cancellationPending = true
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: { current_run_id: 'new-run', cancellation_pending: false }
  }))
  await h.stream.startRunStream('thread', 'new-run', null, { turnId: 'new-turn' })
  assert.equal(h.state.cancellationPending, false)
  assert.equal(h.state.activeRunSteerable, true)
})

test('取消被拒绝时即使同轮 Run 已切换，也能恢复新 Run 的输入', async (t) => {
  const h = harness(t, [])
  let reject
  t.mock.method(
    api,
    'cancelThreadTurn',
    () =>
      new Promise((_, fail) => {
        reject = fail
      })
  )
  const stopping = h.stream.requestTurnCancellation('thread')
  h.state.activeRunId = 'new-run'
  h.state.turnStatus = 'in_progress'
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: { current_run_id: 'new-run', cancellation_pending: false }
  }))
  reject(new Error('当前 Run 已变化'))
  assert.equal(await stopping, false)
  assert.equal(h.state.cancellationPending, false)
  assert.equal(h.state.activeRunSteerable, true)
})

test('取消已提交但响应丢失时，快照保留停止反馈直到终态', async (t) => {
  const h = harness(t, [])
  t.mock.method(api, 'cancelThreadTurn', async () => {
    throw new Error('response lost')
  })
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: { current_run_id: 'run', cancellation_pending: true }
  }))
  assert.equal(await h.stream.requestTurnCancellation('thread'), false)
  assert.equal(h.state.cancellationPending, true)
  assert.equal(h.state.activeRunSteerable, false)
})

test('跨轮重连只恢复新轮的停止事实', async (t) => {
  const h = harness(t, [])
  h.state.cancellationPending = true
  t.mock.method(api, 'getPublicThread', async () => ({
    yuxi: { current_turn: { id: 'new-turn' } }
  }))
  t.mock.method(api, 'getThreadTurn', async () => ({
    status: 'in_progress',
    yuxi: { current_run_id: 'new-run', cancellation_pending: false }
  }))
  await h.stream.resumeActiveRunForThread('thread')
  await tick()
  assert.equal(h.state.currentTurnId, 'new-turn')
  assert.equal(h.state.cancellationPending, false)
  assert.equal(h.state.activeRunSteerable, true)
})

test('取消协调的旧快照失效后，后续有效快照仍能解除请求等待', async (t) => {
  const h = harness(t, [event('agent.session.turn.in_progress')])
  let lookup, releaseLookup
  const lookupStarted = new Promise((resolve) => {
    lookup = resolve
  })
  const snapshot = {
    status: 'in_progress',
    yuxi: { current_run_id: 'run', cancellation_pending: false }
  }
  t.mock.method(api, 'cancelThreadTurn', async () => {
    throw new Error('rejected')
  })
  let calls = 0
  t.mock.method(api, 'getThreadTurn', async () => {
    if (++calls === 1) {
      lookup()
      return new Promise((resolve) => {
        releaseLookup = resolve
      })
    }
    return snapshot
  })
  const stopping = h.stream.requestTurnCancellation('thread')
  await lookupStarted
  await h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  releaseLookup(snapshot)
  await stopping
  assert.equal(h.state.cancellationPending, false)
  assert.equal(h.state.activeRunSteerable, true)
})

test('取消请求及首次快照均失败后，网络恢复可以解除停止等待', async (t) => {
  const h = harness(t, [])
  t.mock.method(api, 'cancelThreadTurn', async () => {
    throw new Error('offline')
  })
  let calls = 0
  t.mock.method(api, 'getThreadTurn', async () => {
    if (++calls === 1) throw new Error('offline')
    return { status: 'in_progress', yuxi: { current_run_id: 'run', cancellation_pending: false } }
  })
  await h.stream.requestTurnCancellation('thread')
  await tick()
  assert.equal(h.state.cancellationPending, false)
  assert.equal(h.state.activeRunSteerable, true)
})
const event = (type, runId = 'run', turnId = 'turn') => ({
  type,
  event_id: `${type}:${runId}`,
  session_id: 'thread',
  turn_id: turnId,
  yuxi: { run_id: runId },
  ...((type.startsWith('agent.session.turn.') &&
    ['created', 'in_progress', 'completed', 'failed', 'cancelled'].includes(
      type.split('.').at(-1)
    )) ||
  type === 'yuxi.session.turn.waiting'
    ? {
        turn: {
          status:
            type === 'yuxi.session.turn.waiting'
              ? 'requires_action'
              : type.endsWith('.created')
                ? 'queued'
                : type.split('.').at(-1)
        }
      }
    : {})
})
const response = (events) =>
  new Response(
    new ReadableStream({
      start(controller) {
        events.forEach((value, index) =>
          controller.enqueue(
            new TextEncoder().encode(
              `id: 2.0.1.${index + 1}-0.3\nevent: ${value.type}\ndata: ${JSON.stringify(value)}\n\n`
            )
          )
        )
        controller.close()
      }
    })
  )
function harness(
  t,
  events,
  { status = 'running', currentRun = 'run', snapshot = async () => {} } = {}
) {
  t.mock.method(ErrorHandler, 'handleError', (error) => error)
  const state = {
    currentTurnId: 'turn',
    activeRunId: currentRun,
    itemState: createItemState()
  }
  const delivered = [],
    terminal = []
  t.mock.method(api, 'getThreadTurn', async () => ({
    id: 'turn',
    object: 'agent.session.turn',
    status: status === 'running' ? 'in_progress' : status,
    yuxi: { current_run_id: currentRun, status }
  }))
  t.mock.method(api, 'streamThreadEvents', async () => response(events))
  const stream = useAgentRunStream({
    getThreadState: () => state,
    currentAgentId: 'agent',
    handlePublicEvent: (value) => delivered.push(value),
    reconcileTurnHistory: snapshot,
    resetThreadExecution() {},
    onTerminalDetected: (value) => terminal.push(value)
  })
  t.after(() => stream.stopRunStreamSubscription('thread'))
  return { state, stream, delivered, terminal }
}

test('SSE 每条 data 直接消费；等待异步快照时缓冲后续事件', async () => {
  const order = []
  const events = [event('yuxi.session.resync'), event('agent.session.turn.output_text.delta')]
  await processRunSseResponse(response(events), async (_type, data) => {
    order.push(data.type)
    if (data.type === 'yuxi.session.resync') {
      await tick()
      order.push('snapshot-applied')
    }
  })
  assert.deepEqual(order, [
    'yuxi.session.resync',
    'snapshot-applied',
    'agent.session.turn.output_text.delta'
  ])
})

test('resync 回读 items 和 Turn 后继续消费终态，cursor 保持订阅位置', async (t) => {
  const order = []
  const h = harness(t, [event('yuxi.session.resync'), event('agent.session.turn.completed')], {
    snapshot: async () => {
      order.push('snapshot')
      await tick()
    }
  })
  await h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  assert.equal(order[0], 'snapshot')
  assert.equal(h.state.turnStatus, 'completed')
  assert.match(h.state.threadCursor, /^2\./)
  assert.equal(h.terminal.length, 1)
})

test('waiting 立即禁发并保留自身 Run，父终态回调不包含子 Thread', async (t) => {
  const h = harness(t, [event('yuxi.session.turn.waiting')])
  await h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  assert.equal(h.state.turnStatus, 'requires_action')
  assert.equal(h.state.activeRunSteerable, false)
  assert.equal(h.state.activeRunId, 'run')
  assert.equal(h.terminal.length, 0)
})

test('旧 Run 的 waiting 和其他 Turn 的终态不能结束恢复后的 Run', async (t) => {
  const h = harness(
    t,
    [
      event('yuxi.session.turn.waiting', 'old'),
      event('agent.session.turn.completed', 'foreign', 'foreign-turn'),
      event('agent.session.turn.completed', 'new')
    ],
    { currentRun: 'new' }
  )
  await h.stream.startRunStream('thread', 'new', null, { turnId: 'turn' })
  await tick()
  assert.equal(h.delivered.length, 1)
  assert.equal(h.delivered[0].yuxi.run_id, 'new')
  assert.equal(h.state.turnStatus, 'completed')
  assert.deepEqual(h.terminal[0].touchedThreadIds, ['thread'])
})

test('同一轮的协作取消可在本地 Run 落后且快照不可用时收尾', async (t) => {
  const cancelled = {
    ...event('agent.session.turn.cancelled', 'resumed'),
    yuxi: { run_id: 'resumed', current_run_id: 'resumed', error_type: 'cooperation_waiting' }
  }
  const wrongOwner = { ...cancelled, yuxi: { ...cancelled.yuxi, run_id: 'superseded' } }
  const h = harness(t, [wrongOwner, cancelled], {
    snapshot: async () => { throw new Error('轮次历史不可用') }
  })
  t.mock.method(api, 'getThreadTurn', async () => { throw new Error('轮次详情不可用') })

  await h.stream.startRunStream('thread', 'old', null, { turnId: 'turn' })
  await tick()

  assert.deepEqual(h.delivered, [cancelled])
  assert.equal(h.state.turnStatus, 'cancelled')
  assert.equal(h.state.isStreaming, false)
  assert.equal(h.state.threadCursor, '2.0.1.2-0.3')
  assert.equal(h.terminal[0].runId, 'resumed')
})

test('Run settled 不结束 Turn；只有 Turn 终态收尾', async (t) => {
  const h = harness(t, [event('yuxi.session.run.settled'), event('agent.session.turn.completed')])
  await h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  assert.equal(h.delivered.length, 2)
  assert.equal(h.terminal.length, 1)
})

test('旧 Turn 等待历史回读时启动新 FIFO 流，旧收尾不能中止新订阅或审批', async (t) => {
  let releaseHistory
  const history = new Promise((resolve) => {
    releaseHistory = resolve
  })
  let secondController,
    resetCount = 0,
    terminalCount = 0
  const state = { currentTurnId: 'turn', activeRunId: 'run', itemState: createItemState() }
  t.mock.method(api, 'getThreadTurn', async (_thread, turnId) => ({
    id: turnId,
    yuxi: { current_run_id: 'next', status: 'running' }
  }))
  t.mock.method(api, 'streamThreadEvents', async (_thread, _cursor, { signal }) => {
    if (!secondController) {
      secondController = signal
      return response([event('agent.session.turn.completed')])
    }
    secondController = signal
    return new Response(
      new ReadableStream({
        start(controller) {
          signal.addEventListener('abort', () => controller.close(), { once: true })
        }
      })
    )
  })
  const stream = useAgentRunStream({
    getThreadState: () => state,
    currentAgentId: 'agent',
    handlePublicEvent() {},
    reconcileTurnHistory: () => history,
    resetThreadExecution: () => {
      resetCount++
      state.runStreamAbortController?.abort()
    },
    onTerminalDetected: () => terminalCount++
  })
  await stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  const following = stream.startRunStream('thread', 'next', null, { turnId: 'next-turn' })
  await tick()
  releaseHistory()
  await tick()
  assert.equal(state.currentTurnId, 'next-turn')
  assert.equal(secondController.aborted, false)
  assert.equal(resetCount, 0)
  assert.equal(terminalCount, 0)
  stream.stopRunStreamSubscription('thread')
  await following
})

test('协作等待保留 SSE，自动恢复的 Run 重新允许 steer 并接收最终输出', async (t) => {
  const h = harness(t, [])
  let controller,
    signal,
    currentRun = 'run'
  t.mock.method(api, 'getThreadTurn', async () => ({
    id: 'turn',
    object: 'agent.session.turn',
    status: 'in_progress',
    yuxi: { current_run_id: currentRun, status: 'running' }
  }))
  t.mock.method(api, 'streamThreadEvents', async (_id, _cursor, options) => {
    signal = options.signal
    return new Response(
      new ReadableStream({
        start(value) {
          controller = value
        }
      })
    )
  })
  const emit = (value) =>
    controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(value)}\n\n`))
  const subscribed = h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  emit({
    ...event('yuxi.session.turn.waiting'),
    turn: { status: 'in_progress' },
    waitpoint: { kind: 'cooperation' }
  })
  await tick()
  assert.equal(h.state.turnStatus, 'in_progress')
  assert.equal(h.state.activeRunSteerable, false)
  assert.equal(signal.aborted, false)
  h.state.queuedInputs ||= []
  h.state.itemState ||= createItemState()
  h.state.optimisticInputs ||= {}
  h.state.queuedInputs.push({
    input_id: 'queued-input',
    status: 'pending',
    content: '队列里的新工作'
  })
  assert.equal(signal.aborted, false, '协作等待保留当前 Turn 的 SSE')
  currentRun = 'resumed'
  emit(event('agent.session.turn.in_progress', 'resumed'))
  await tick()
  assert.equal(h.state.activeRunId, 'resumed')
  assert.equal(h.state.turnStatus, 'in_progress')
  assert.equal(h.state.isStreaming, true)
  assert.equal(h.state.activeRunSteerable, true)
  assert.equal(h.state.cooperationWaiting, false)
  emit({ ...event('agent.session.turn.output_text.delta', 'resumed'), delta: 'result' })
  emit(event('agent.session.turn.completed', 'resumed'))
  controller.close()
  await subscribed
  await tick()
  assert.equal(h.state.turnStatus, 'completed')
  assert.equal(h.delivered.find((value) => value.delta)?.delta, 'result')
  assert.equal(h.terminal.length, 1)
})

test('静默断线超过一分钟后从持久 Turn 收尾并读取最终产物', async (t) => {
  const h = harness(t, [], { status: 'completed', snapshot: async () => { h.delivered.push('final-artifacts') } })
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let cancelled = false
  t.mock.method(api, 'streamThreadEvents', async () => new Response(new ReadableStream({
    cancel() { cancelled = true }
  })))
  const running = h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  t.mock.timers.tick(60001)
  await tick()
  assert.equal(h.state.turnStatus, 'completed')
  assert.equal(h.state.isStreaming, false)
  assert.deepEqual(h.delivered, ['final-artifacts'])
  assert.equal(h.terminal.length, 1)
  assert.equal(cancelled, true)
  await running
})

test('正常心跳字节持续重置超时，长任务不因没有正文而断开', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let source
  const reading = processRunSseResponse(new Response(new ReadableStream({ start(c) { source = c } })), async () => {})
  let finished = false
  reading.finally(() => { finished = true })
  for (let i = 0; i < 6; i++) {
    t.mock.timers.tick(15000)
    source.enqueue(new TextEncoder().encode(': heartbeat\n\n'))
    await tick()
    assert.equal(finished, false)
  }
  source.close()
  await reading
})

test('静默断线时任务仍在运行则按原游标重连，停止订阅后不再重连', async (t) => {
  const h = harness(t, [])
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const cursors = []
  t.mock.method(api, 'streamThreadEvents', async (_threadId, cursor) => {
    cursors.push(cursor)
    return new Response(new ReadableStream())
  })
  const running = h.stream.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  h.state.threadCursor = '2.0.1.19-0.3'
  t.mock.timers.tick(60001)
  await running
  assert.equal(h.state.isStreaming, true)
  assert.equal(h.terminal.length, 0)
  t.mock.timers.tick(5000)
  await tick()
  assert.deepEqual(cursors, [undefined, '2.0.1.19-0.3'])
  h.stream.stopRunStreamSubscription('thread')
  t.mock.timers.tick(65000)
  await tick()
  assert.equal(cursors.length, 2)
})
