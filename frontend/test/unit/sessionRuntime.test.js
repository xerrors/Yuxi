import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { setImmediate } from 'node:timers'
import { createTestServer } from '../helpers/createTestServer.js'
import { createPinia, setActivePinia, disposePinia } from 'pinia'
import { message as toast } from 'ant-design-vue'

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
const tick = () => new Promise((resolve) => setImmediate(resolve))

function setup(t) {
  t.mock.method(ErrorHandler, 'handleError', () => {})
  const pinia = createPinia()
  setActivePinia(pinia)
  const saved = { ...api }
  let subscriptions = 0,
    lookups = 0,
    aborted = 0,
    wire
  Object.assign(api, {
    getPublicThread: async () => {
      lookups++
      return { yuxi: { current_turn: { id: 'turn' } } }
    },
    getThreadTurn: async () => ({ id: 'turn', status: 'in_progress', yuxi: { current_run_id: 'run', status: 'running' } }),
    getSessionItems: async () => ({ data: [], yuxi: { runs: [] }, has_more: false, last_id: null }),
    getAgentState: async () => ({ agent_state: {} }),
    getThreadQueue: async () => ({ inputs: [], status: 'ready' }),
    streamThreadEvents: async (_id, _after, { signal }) => {
      subscriptions++
      const body = new ReadableStream({
        start(controller) {
          wire = controller
        }
      })
      signal.addEventListener(
        'abort',
        () => {
          aborted++
          wire.close()
        },
        { once: true }
      )
      return new Response(body)
    }
  })
  const runtime = useRuntime()
  t.after(() => {
    disposePinia(pinia)
    Object.assign(api, saved)
  })
  return {
    runtime,
    counts: () => ({ subscriptions, lookups, aborted }),
    emit: (event) => wire.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
  }
}

/** 通过实际 SSE 消费入口驱动思考展示，原始 item 与展示结果分别断言。 */
async function setupReasoningStream(t) {
  t.mock.method(toast, 'error', () => {})
  const harness = setup(t)
  const detach = harness.runtime.observeThread('thread', {})
  void harness.runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  t.after(detach)
  let sequence = 0
  const send = async (fields) => {
    harness.emit({ session_id: 'thread', turn_id: 'turn', yuxi: { run_id: 'run' },
      event_id: `reasoning-event-${++sequence}`, ...fields })
    await tick()
  }
  const item = { id: 'thinking', type: 'message', role: 'assistant', status: 'in_progress',
    turn_id: 'turn', content: [], yuxi: { run_id: 'run', message_id: 1, output_index: 0 } }
  await send({ type: 'agent.session.turn.item.added', item })
  const reasoning = (delta, content_index = 0) => send({
    type: 'yuxi.session.turn.reasoning.delta', item_id: item.id, content_index, delta
  })
  return { ...harness, send, item, reasoning,
    state: harness.runtime.getThreadState('thread'),
    visible: () => harness.runtime.threadMessages.thread.filter(message => message.reasoning_content)
  }
}

test('流式只展示当前思考段，完成后隐藏且原始文本完整', async (t) => {
  const h = await setupReasoningStream(t)
  await h.reasoning('先检查')
  await h.reasoning('输入')
  assert.equal(h.visible()[0].reasoning_content, '先检查输入')
  await h.send({ type: 'yuxi.session.turn.state', agent_state: { todos: [] } })
  await h.reasoning('')
  await h.send({ type: 'agent.session.turn.output_text.delta', item_id: h.item.id,
    content_index: 0, delta: '' })
  assert.equal(h.visible()[0].reasoning_content, '先检查输入')
  await h.send({ type: 'yuxi.session.turn.reasoning.done', item_id: h.item.id,
    content_index: 0, text: '先检查输入，已完成' })
  assert.deepEqual(h.visible(), [])
  await h.reasoning('迟到增量')
  assert.deepEqual(h.visible(), [])
  assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[0], '先检查输入，已完成')
  await h.reasoning('另一段', 2)
  assert.equal(h.visible()[0].reasoning_content, '另一段')
  assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[0], '先检查输入，已完成')
  await h.send({ type: 'agent.session.turn.item.done', item: {
    ...h.state.itemState.items.thinking, status: 'completed'
  } })
  assert.equal(h.state.isStreaming, true)
  assert.deepEqual(h.visible(), [], '模型消息结束时不等待整轮结束才隐藏')
})

test('没有 reasoning.done 时，正文或工具开始仍隐藏当前思考', async (t) => {
  const h = await setupReasoningStream(t)
  for (const [index, transition] of [
    { type: 'agent.session.turn.content_part.added', item_id: h.item.id,
      content_index: 0, part: { type: 'output_text', text: '' } },
    { type: 'agent.session.turn.output_text.delta', item_id: h.item.id,
      content_index: 0, delta: '回答正文' },
    { type: 'agent.session.turn.output_text.done', item_id: h.item.id,
      content_index: 0, text: '完整回答正文' },
    { type: 'agent.session.turn.content_part.done', item_id: h.item.id,
      content_index: 0, part: { type: 'output_text', text: '完整回答正文' } },
    { type: 'agent.session.turn.item.added', item: { id: 'tool', type: 'function_call',
      name: 'read_file', call_id: 'call', arguments: {}, status: 'in_progress', turn_id: 'turn',
      yuxi: { run_id: 'run', message_id: 2, output_index: 1 } } }
  ].entries()) {
    await h.reasoning('正在分析', index)
    assert.equal(h.visible().length, 1)
    await h.send(transition)
    assert.deepEqual(h.visible(), [], transition.type)
    await h.reasoning('迟到增量', index)
    assert.deepEqual(h.visible(), [], `${transition.type} 后旧段不能重新出现`)
    assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[index], '正在分析迟到增量')
  }
  const messages = h.runtime.threadMessages.thread
  assert.equal(messages[0].content, '完整回答正文')
  assert.equal(messages[1].tool_calls[0].name, 'read_file')
  assert.ok(h.state.itemState.items.thinking.yuxi.reasoning[0])
})

test('新思考替换旧思考，重复事件和其他 Run 事件不改变当前展示', async (t) => {
  const h = await setupReasoningStream(t)
  await h.reasoning('旧段')
  const tool = { type: 'agent.session.turn.item.added', item: { id: 'old-tool',
    type: 'function_call', name: 'read_file', call_id: 'old-call', arguments: {},
    status: 'in_progress', turn_id: 'turn', yuxi: { run_id: 'run', message_id: 2 } } }
  await h.send(tool)
  const old = { type: 'yuxi.session.turn.reasoning.delta', item_id: h.item.id,
    content_index: 0, delta: '旧事件', event_id: 'same-event' }
  await h.send(old)
  await h.reasoning('当前段', 2)
  await h.send(tool)
  await h.send(old)
  await h.send({ type: 'agent.session.turn.item.added', item: {
    ...h.item, id: 'other-run', yuxi: { run_id: 'old-run', message_id: 2 }
  } })
  await h.send({ type: 'yuxi.session.turn.reasoning.delta', item_id: 'other-run',
    content_index: 0, delta: '其他运行', yuxi: { run_id: 'old-run' } })
  await h.send({ type: 'agent.session.turn.output_text.delta', item_id: 'other-run',
    content_index: 0, delta: '其他正文', yuxi: { run_id: 'old-run' } })
  assert.equal(h.visible().length, 1)
  assert.equal(h.visible()[0].reasoning_content, '当前段')
  await h.send({ type: 'yuxi.session.turn.reasoning.done', item_id: h.item.id,
    content_index: 0, text: '旧段完成' })
  assert.equal(h.visible()[0].reasoning_content, '当前段')
  await h.reasoning('新段', 4)
  await h.reasoning('迟到增量', 2)
  assert.equal(h.visible()[0].reasoning_content, '新段', '旧段不能抢回新段的展示')
  assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[2], '当前段迟到增量')
})

test('断线恢复的完成索引关闭旧思考，历史快照不会制造正在思考', async (t) => {
  const h = await setupReasoningStream(t)
  await h.reasoning('未完成片段')
  const snapshot = { ...h.item, yuxi: { ...h.item.yuxi,
    reasoning: { 0: '完整思考' }, completed_reasoning_indices: [0] } }
  api.getSessionItems = async () => ({ data: [snapshot], yuxi: { runs: [] }, has_more: false })
  await h.send({ type: 'yuxi.session.resync' })
  await tick()
  assert.deepEqual(h.visible(), [])
  assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[0], '完整思考')
  await h.reasoning('重放旧片段')
  assert.deepEqual(h.visible(), [])
  h.runtime.resetThreadExecution('thread')
  await h.runtime.refreshThreadHistory('thread')
  assert.deepEqual(h.visible(), [])
  assert.equal(h.state.activeReasoning, null)
})

for (const status of ['completed', 'failed', 'cancelled', 'requires_action']) {
  test(`运行 ${status} 后回读包含思考的历史也不显示`, async (t) => {
    const h = await setupReasoningStream(t)
    await h.reasoning('终态前思考')
    const snapshot = { ...h.item, status: status === 'failed' ? 'failed' : 'completed',
      content: [{ type: 'output_text', text: '保留正文' }],
      yuxi: { ...h.item.yuxi, reasoning: { 0: '完整思考' } } }
    api.getSessionItems = async () => ({ data: [snapshot], yuxi: { runs: [] }, has_more: false })
    await h.send({ type: `agent.session.turn.${status}`, turn: { id: 'turn', status,
      yuxi: { waitpoint: { kind: 'question', id: 'waiting' } } } })
    await tick()
    assert.equal(h.state.isStreaming, false)
    assert.deepEqual(h.visible(), [])
    assert.equal(h.runtime.threadMessages.thread[0].content, '保留正文')
    assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[0], '完整思考')
  })
}

test('取消请求和协作等待撤下思考，不删除协议内容', async (t) => {
  const h = await setupReasoningStream(t)
  await h.reasoning('保留的原文')
  for (const flag of ['cancellationPending', 'cooperationWaiting']) {
    h.state[flag] = true
    assert.deepEqual(h.visible(), [], flag)
    h.state[flag] = false
  }
  assert.equal(h.state.itemState.items.thinking.yuxi.reasoning[0], '保留的原文')
})

test('首次观察与清理后重建声明运行版本、协作等待和审批默认值，并隔离 Thread', (t) => {
  const { runtime } = setup(t)
  const detach = runtime.observeThread('thread', {})
  const detachOther = runtime.observeThread('other', {})
  const original = runtime.getThreadState('thread')
  assert.equal(original.runStateVersion, 0)
  assert.equal(original.cooperationWaiting, false)
  assert.equal(original.approvalSubmitting, false)
  original.runStateVersion = 3
  original.cooperationWaiting = true
  original.approvalSubmitting = true
  const other = runtime.getThreadState('other')
  assert.equal(other.runStateVersion, 0)
  assert.equal(other.cooperationWaiting, false)
  assert.equal(other.approvalSubmitting, false)
  detach()
  const detachNew = runtime.observeThread('thread', {})
  const current = runtime.getThreadState('thread')
  assert.notEqual(current, original)
  assert.equal(current.runStateVersion, 0)
  assert.equal(current.cooperationWaiting, false)
  assert.equal(current.approvalSubmitting, false)
  detachNew()
  detachOther()
})

test('页面和侧栏共享一个流及恢复请求，卸载一个视图不停止另一个', async (t) => {
  const h = setup(t),
    { runtime } = h
  const first = runtime.observeThread('thread', {})
  const second = runtime.observeThread('thread', {})
  await Promise.all([
    runtime.resumeActiveRunForThread('thread'),
    runtime.resumeActiveRunForThread('thread')
  ])
  await tick()
  assert.deepEqual(h.counts(), { subscriptions: 1, lookups: 1, aborted: 0 })
  const state = runtime.getThreadState('thread')
  first()
  h.emit({
    type: 'yuxi.session.turn.state',
    session_id: 'thread',
    turn_id: 'turn',
    yuxi: { run_id: 'run' },
    agent_state: { todos: ['shared after detach'] }
  })
  await tick()
  assert.deepEqual(state.agentState.todos, ['shared after detach'])
  assert.equal(h.counts().aborted, 0)
  second()
  await tick()
  assert.equal(h.counts().aborted, 1)
  assert.equal(runtime.threadMessages.thread, undefined)
})

test('重复启动同一轮不重连，另一视图读取同一份持久历史', async (t) => {
  const h = setup(t),
    { runtime } = h
  const detach = runtime.observeThread('thread', {})
  const detach2 = runtime.observeThread('thread', {})
  let histories = 0
  api.getSessionItems = async () => {
    histories++
    return { data: [], yuxi: { runs: [{ id: 'result-run' }] }, has_more: false, last_id: null }
  }
  const stream = runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  await runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await Promise.all([
    runtime.refreshThreadHistory('thread'),
    runtime.refreshThreadHistory('thread')
  ])
  assert.equal(histories, 1)
  assert.equal(runtime.threadRuns.thread[0].id, 'result-run')
  assert.equal(h.counts().subscriptions, 1)
  detach()
  detach2()
  await stream
})

test('最后一个视图释放后，迟到恢复和历史不能启动流或覆盖新实例', async (t) => {
  const h = setup(t),
    { runtime } = h
  const detach = runtime.observeThread('thread', {})
  let releaseHistory
  const lookupResolvers = []
  api.getPublicThread = () =>
    new Promise((resolve) => {
      lookupResolvers.push(resolve)
    })
  api.getSessionItems = () =>
    new Promise((resolve) => {
      releaseHistory = resolve
    })
  const resume = runtime.resumeActiveRunForThread('thread')
  const history = runtime.refreshThreadHistory('thread')
  await tick()
  detach()
  const detachNew = runtime.observeThread('thread', {})
  runtime.getThreadState('thread').historyLoaded = true
  const newItems = runtime.getThreadState('thread').itemState
  for (const release of lookupResolvers) release({ yuxi: { current_turn: { id: 'old-turn' } } })
  releaseHistory({ data: [], yuxi: { runs: [{ id: 'old-run' }] }, has_more: false, last_id: null })
  await Promise.all([resume, history])
  assert.equal(h.counts().subscriptions, 0)
  assert.equal(runtime.getThreadState('thread').itemState, newItems)
  assert.deepEqual(runtime.threadMessages.thread, [])
  detachNew()
})

test('同一 Thread 的旧恢复快照不能覆盖刚开始的新执行', async (t) => {
  const h = setup(t),
    { runtime } = h
  const detach = runtime.observeThread('thread', {})
  let releaseLookup
  api.getPublicThread = () =>
    new Promise((resolve) => {
      releaseLookup = resolve
    })
  const restoring = runtime.resumeActiveRunForThread('thread')
  await tick()
  const stream = runtime.startRunStream('thread', 'new-run', null, { turnId: 'new-turn' })
  await tick()
  releaseLookup({ yuxi: { current_turn: null } })
  await restoring
  const state = runtime.getThreadState('thread')
  assert.equal(state.activeRunId, 'new-run')
  assert.equal(state.currentTurnId, 'new-turn')
  assert.equal(state.isStreaming, true)
  detach()
  await stream
})

for (const historyFails of [false, true]) {
  test(`终态必须重读完成后的历史，读取${historyFails ? '失败保留流输出' : '成功保留最终输出'}`, async (t) => {
    const h = setup(t),
      { runtime } = h
    const detach = runtime.observeThread('thread', {})
    let resolveOld,
      requests = 0
    const item = {
      id: 'output',
      type: 'message',
      role: 'assistant',
      turn_id: 'turn',
      status: 'completed',
      content: [{ type: 'output_text', text: 'FINAL OUTPUT' }],
      yuxi: { run_id: 'run', message_id: 'output' }
    }
    api.getSessionItems = async () => {
      requests++
      if (requests === 1)
        return new Promise((resolve) => {
          resolveOld = resolve
        })
      if (historyFails) throw new Error('history unavailable')
      return {
        data: [item], yuxi: { runs: [] }, has_more: false, last_id: item.id
      }
    }
    const old = runtime.refreshThreadHistory('thread')
    await tick()
    const stream = runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
    await tick()
    h.emit({
      type: 'agent.session.turn.item.done',
      event_id: 'done-output',
      session_id: 'thread',
      turn_id: 'turn',
      yuxi: { run_id: 'run' },
      item
    })
    h.emit({
      type: 'agent.session.turn.completed',
      event_id: 'completed',
      session_id: 'thread',
      turn_id: 'turn',
      yuxi: { run_id: 'run', current_run_id: 'run' },
      turn: { status: 'completed' }
    })
    await stream
    resolveOld({ data: [], yuxi: { runs: [] }, has_more: false, last_id: null })
    await old
    await tick()
    await tick()
    assert.equal(requests, 2, '终态不能共用完成事件之前的历史')
    const output = historyFails
      ? runtime.getThreadState('thread').itemState.items.output.content[0].text
      : runtime.threadMessages.thread[0].content
    assert.equal(output, 'FINAL OUTPUT')
    detach()
  })
}

test('旧终态历史完成时不能清空重挂载实例的新流', async (t) => {
  const h = setup(t),
    { runtime } = h
  const detach = runtime.observeThread('thread', {})
  let resolveHistory
  api.getSessionItems = () =>
    new Promise((resolve) => {
      resolveHistory = resolve
    })
  const old = runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  h.emit({
    type: 'agent.session.turn.completed',
    event_id: 'completed',
    session_id: 'thread',
    turn_id: 'turn',
    yuxi: { run_id: 'run', current_run_id: 'run' },
    turn: { status: 'completed' }
  })
  await old
  await tick()
  detach()
  const detachNew = runtime.observeThread('thread', {})
  const active = runtime.startRunStream('thread', 'new-run', null, { turnId: 'new-turn' })
  await tick()
  resolveHistory({ data: [], yuxi: { runs: [] }, has_more: false, last_id: null })
  await tick()
  await tick()
  assert.equal(runtime.getThreadState('thread').activeRunId, 'new-run')
  assert.equal(runtime.getThreadState('thread').runStreamAbortController.signal.aborted, false)
  assert.equal(h.counts().aborted, 1, '旧终态只停止旧流')
  detachNew()
  await active
})

test('同一 SSE 内进入新 Run 后，旧恢复快照不能恢复旧等待点', async (t) => {
    const h = setup(t),
      { runtime } = h
    const detach = runtime.observeThread('thread', {})
    const stream = runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
    await tick()
    let resolveOld,
      reads = 0
    api.getThreadTurn = async () => {
      if (++reads === 1)
        return new Promise((resolve) => {
          resolveOld = resolve
        })
      return { id: 'turn', status: 'in_progress', yuxi: { current_run_id: 'run2', status: 'running' } }
    }
    const restoring = runtime.resumeActiveRunForThread('thread')
    await tick()
    await tick()
    assert.equal(typeof resolveOld, 'function')
    h.emit({
      type: 'agent.session.turn.in_progress',
      session_id: 'thread',
      turn_id: 'turn',
      yuxi: { run_id: 'run2', current_run_id: 'run2' }, turn: { status: 'in_progress' }
    })
    await tick()
    await tick()
    assert.equal(runtime.getThreadState('thread').activeRunId, 'run2')
    resolveOld({ id: 'turn', status: 'requires_action', yuxi: {
      current_run_id: 'run',
      status: 'waiting',
      waitpoint: {
        kind: 'answer',
        id: 'old-waitpoint',
        run_id: 'run',
        questions: [{ question: 'old question' }]
      }
    } })
    await restoring
    await tick()
    await tick()
    const state = runtime.getThreadState('thread')
    assert.equal(state.activeRunId, 'run2')
    assert.equal(state.isStreaming, true)
    assert.equal(state.turnStatus, 'in_progress')
    assert.equal(state.pendingInterrupt, null)
    assert.equal(h.counts().subscriptions, 1)
    detach()
    await stream
})

test('SSE 校验旧 Run 期间的新恢复结果不能被旧事件覆盖', async (t) => {
  const h = setup(t),
    { runtime } = h
  const detach = runtime.observeThread('thread', {})
  const stream = runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  let releaseOld,
    reads = 0
  api.getThreadTurn = async () => {
    if (++reads === 1)
      return new Promise((resolve) => {
        releaseOld = resolve
      })
    return { id: 'turn', status: 'in_progress', yuxi: { status: 'running', current_run_id: 'run3' } }
  }
  h.emit({
    type: 'agent.session.turn.in_progress',
    session_id: 'thread',
    turn_id: 'turn',
    yuxi: { run_id: 'run2', current_run_id: 'run2' }, turn: { status: 'in_progress' }
  })
  await tick()
  await runtime.resumeActiveRunForThread('thread')
  assert.equal(runtime.getThreadState('thread').activeRunId, 'run3')
  releaseOld({ id: 'turn', status: 'in_progress', yuxi: { status: 'running', current_run_id: 'run2' } })
  await tick()
  await tick()
  assert.equal(runtime.getThreadState('thread').activeRunId, 'run3')
  detach()
  await stream
})

test('旧 checkpoint 查询不能覆盖新 Run 的状态事件', async (t) => {
  const h = setup(t),
    { runtime } = h
  const detach = runtime.observeThread('thread', {})
  let releaseState
  api.getAgentState = () =>
    new Promise((resolve) => {
      releaseState = resolve
    })
  const old = runtime.fetchAgentState('thread')
  await tick()
  const stream = runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  h.emit({
    type: 'yuxi.session.turn.state',
    session_id: 'thread',
    turn_id: 'turn',
    yuxi: { run_id: 'run' },
    agent_state: { todos: ['new state'] }
  })
  await tick()
  releaseState({ agent_state: { todos: ['old state'] } })
  await old
  assert.deepEqual(runtime.getThreadState('thread').agentState.todos, ['new state'])
  detach()
  await stream
})

test('恢复人类操作使用持久等待点，状态面板的 checkpoint 不覆盖审批', async (t) => {
  const { runtime } = setup(t)
  t.after(runtime.observeThread('thread', {}))
  api.getThreadTurn = async () => ({
    id: 'turn', status: 'requires_action',
    yuxi: {
      current_run_id: 'run',
      waitpoint: { id: 'waitpoint', run_id: 'run', kind: 'answer', questions: [{ question_id: 'q' }] },
      output: [], runs: []
    }
  })
  const previous = Promise.withResolvers()
  api.getAgentState = t.mock.fn()
  api.getAgentState.mock.mockImplementationOnce(() => previous.promise)
  api.getAgentState.mock.mockImplementation(async () => ({
    agent_state: { todos: ['面板任务'] },
    interrupt: { run_id: 'run', questions: [{ question_id: 'old' }] }
  }))
  const oldState = runtime.fetchAgentState('thread')
  await tick()
  await runtime.restoreThreadWaitpoint('thread')
  const freshState = runtime.fetchAgentState('thread')
  assert.notEqual(freshState, oldState, '等待点必须独立读取面板状态')
  assert.equal(await freshState, true)
  previous.resolve({ agent_state: { todos: ['旧的面板任务'] } })
  assert.equal(await oldState, false)
  const state = runtime.getThreadState('thread')
  assert.equal(state.pendingInterrupt.waitpointId, 'waitpoint')
  assert.deepEqual(state.pendingInterrupt.questions, [{ question_id: 'q' }])
  assert.deepEqual(state.agentState.todos, ['面板任务'])
})

test('分页历史合并更早内容，刷新跨过断线缺口且保留已加载页面', async (t) => {
  const { runtime } = setup(t)
  const detach = runtime.observeThread('thread', {})
  api.getPublicThread = async () => ({ id: 'thread', yuxi: { current_turn: null } })
  const item = (id) => ({
    id: String(id),
    type: 'message',
    role: 'user',
    status: 'completed',
    phase: null,
    content: [{ type: 'input_text', text: `message ${id}` }],
    yuxi: { message_id: id }
  })
  let latest = 9
  const calls = []
  api.getSessionItems = async (_id, { after }) => {
    calls.push(after)
    const index = after ? Number(after) - 1 : latest
    const data = [index, index - 1].filter((id) => id > 0).map(item)
    return { data, last_id: data.at(-1)?.id || null, has_more: index > 2, yuxi: { runs: [] } }
  }
  await runtime.refreshThreadHistory('thread')
  assert.deepEqual(
    runtime.threadMessages.thread.map((message) => message.content),
    Array.from({ length: 6 }, (_, index) => `message ${index + 4}`)
  )
  assert.equal(runtime.historyPages.thread.hasMore, true)
  await runtime.loadOlderHistory('thread')
  assert.equal(runtime.threadMessages.thread[0].content, 'message 1')
  latest = 17
  await runtime.refreshThreadHistory('thread')
  assert.deepEqual(
    runtime.threadMessages.thread.map((message) => message.content),
    Array.from({ length: 17 }, (_, index) => `message ${index + 1}`)
  )
  assert.deepEqual(calls, [undefined, '8', '6', '4', '2', undefined, '16', '14', '12', '10'])
  detach()
})

test('指定旧 Turn 的内容和结果不替代新运行，也不改变会话分页游标', async (t) => {
  const { runtime } = setup(t)
  t.after(runtime.observeThread('thread', {}))
  const item = (id, turnId, content, role = 'user') => ({
    id: String(id), turn_id: turnId, type: 'message', role, status: 'completed',
    phase: role === 'assistant' ? 'final_answer' : null,
    content: [{ type: role === 'user' ? 'input_text' : 'output_text', text: content }],
    yuxi: { message_id: id, run_id: `run-${turnId}` }
  })
  api.getPublicThread = async () => ({ id: 'thread', yuxi: { current_turn: { id: 'next' } } })
  api.getThreadTurn = async (_id, turnId) => ({
    id: turnId,
    status: turnId === 'old' ? 'completed' : 'in_progress',
    yuxi: {
      current_run_id: `run-${turnId}`,
      result_run_id: turnId === 'old' ? 'run-old' : null,
      output: turnId === 'old' ? [item(3, 'old', '指定轮次的结果', 'assistant')] : [],
      runs: [{ id: `run-${turnId}`, turn_id: turnId, status: turnId === 'old' ? 'completed' : 'running' }]
    }
  })
  const reads = []
  api.getSessionItems = async (_id, { turnId, after }) => {
    reads.push([turnId, after])
    const data = turnId === 'old'
      ? [item(2, 'old', '指定轮次的输入')]
      : [item(after ? Number(after) - 1 : 6, 'next', '会话窗口')]
    return { data, last_id: data[0].id, has_more: !turnId && Number(data[0].id) > 4, yuxi: { runs: [] } }
  }
  await runtime.loadThread('thread')
  assert.equal(runtime.getThreadState('thread').currentTurnId, 'next')
  assert.equal(runtime.historyPages.thread.after, '4')
  const earlier = await runtime.reconcileTurnHistory('thread', 'old')
  assert.equal(earlier.yuxi.result_run_id, 'run-old')
  assert.equal(runtime.historyPages.thread.after, '4')
  assert.equal(runtime.getThreadState('thread').currentTurnId, 'next')
  assert.equal(runtime.getThreadState('thread').activeRunId, 'run-next')
  assert.deepEqual(runtime.threadMessages.thread.map(({ content }) => content),
    ['指定轮次的输入', '指定轮次的结果', '会话窗口', '会话窗口', '会话窗口'])
  assert.deepEqual(reads, [[undefined, undefined], [undefined, '6'], [undefined, '5'], ['old', undefined]])
})

test('在途读取不能覆盖更新的授权详情，详情读取本身不会加入侧栏', async (t) => {
  const { runtime } = setup(t)
  const { useChatThreadsStore } = await server.ssrLoadModule('/src/modules/session/model/chatThreads.js')
  const sidebar = useChatThreadsStore()
  t.after(runtime.observeThread('thread', {}))
  const started = Promise.withResolvers()
  const request = Promise.withResolvers()
  api.getPublicThread = () => {
    started.resolve()
    return request.promise
  }
  const refresh = runtime.refreshThreadHistory('thread')
  await started.promise
  const state = runtime.peekThreadState('thread')
  assert.equal(runtime.applyThreadDetail('thread', {
    id: 'thread', agent: { id: 'a' }, status: 'idle',
    yuxi: { title: '已保存的标题', archived: true, current_turn: null }
  }, state), true)
  request.resolve({
    id: 'thread', agent: { id: 'a' }, status: 'idle',
    yuxi: { title: '旧标题', archived: false, current_turn: null }
  })
  await refresh
  assert.equal(state.detail.yuxi.title, '已保存的标题')
  assert.equal(state.detail.yuxi.archived, true)
  assert.deepEqual(sidebar.threads, [])
  assert.equal(runtime.applyThreadDetail('inactive', { id: 'inactive', yuxi: {} }), false)
  assert.equal(runtime.peekThreadState('inactive'), null)
})

test('已查看回执与会话修改分别发布未读和元数据', async (t) => {
  const { runtime } = setup(t)
  const { useChatThreadsStore } = await server.ssrLoadModule('/src/modules/session/model/chatThreads.js')
  const sidebar = useChatThreadsStore()
  t.after(runtime.observeThread('thread', {}))
  const detail = (title, unread, status = 'in_progress') => ({
    id: 'thread', agent: { id: 'a', model: 'model' }, status,
    yuxi: { title, unread, archived: false, current_turn: null }
  })
  const original = detail('原有标题', true)
  sidebar.threads = [original]
  const state = runtime.peekThreadState('thread')
  runtime.applyThreadDetail('thread', original, state)
  const started = Promise.withResolvers()
  const request = Promise.withResolvers()
  api.getPublicThread = () => {
    started.resolve()
    return request.promise
  }
  const refresh = runtime.refreshThreadHistory('thread')
  await started.promise
  runtime.applyThreadViewed('thread', detail('已查看回执中的旧标题', false, 'completed'), state)
  assert.equal(state.detail.yuxi.title, '原有标题')
  assert.equal(sidebar.threads[0].status, 'in_progress')
  request.resolve(detail('授权读取中的新标题', true))
  await refresh
  assert.equal(state.detail.yuxi.title, '授权读取中的新标题')
  assert.equal(state.detail.yuxi.unread, false)
  assert.equal(sidebar.threads[0].yuxi.unread, false)
  runtime.applyThreadDetail('thread', detail('用户保存的新标题', true, 'completed'), state)
  assert.equal(state.detail.yuxi.title, '用户保存的新标题')
  assert.equal(state.detail.yuxi.unread, false)
  assert.equal(state.detail.status, 'in_progress')
})

test('首次和追加各读取最多三页，短历史停止且并发追加共享同一窗口', async (t) => {
  const { runtime } = setup(t)
  const detach = runtime.observeThread('thread', {})
  api.getPublicThread = async () => ({ id: 'thread', yuxi: { current_turn: null } })
  const calls = []
  api.getSessionItems = async (_id, { after, limit = 100 }) => {
    calls.push({ after, limit })
    const newest = after ? Number(after) - 1 : 650
    const data = Array.from({ length: Math.min(limit, newest) }, (_, index) => {
      const id = newest - index
      return {
        id: String(id),
        type: 'message',
        role: 'user',
        status: 'completed',
        content: [{ type: 'input_text', text: `message ${id}` }],
        yuxi: { message_id: id }
      }
    })
    return { data, last_id: data.at(-1).id, has_more: newest > limit, yuxi: { runs: [] } }
  }
  await runtime.refreshThreadHistory('thread')
  assert.equal(runtime.threadMessages.thread.length, 300)
  assert.equal(runtime.threadMessages.thread[0].content, 'message 351')
  assert.equal(runtime.historyPages.thread.after, '351')
  await Promise.all([
    runtime.loadOlderHistory('thread'),
    runtime.loadOlderHistory('thread')
  ])
  assert.equal(runtime.threadMessages.thread.length, 600)
  assert.equal(runtime.historyPages.thread.after, '51')
  await runtime.loadOlderHistory('thread')
  assert.equal(runtime.threadMessages.thread.length, 650)
  assert.equal(runtime.threadMessages.thread[0].content, 'message 1')
  assert.equal(runtime.threadMessages.thread.at(-1).content, 'message 650')
  assert.equal(runtime.historyPages.thread.hasMore, false)
  assert.deepEqual(
    calls,
    [undefined, '551', '451', '351', '251', '151', '51'].map((after) => ({ after, limit: 100 }))
  )
  detach()
})

test('首次打开已结束的长轮次只补未读尾页，交错的其他轮次仍能向上翻到', async (t) => {
  const { runtime } = setup(t)
  const { useSessionFeedbackStore } = await server.ssrLoadModule('/src/modules/session/model/sessionFeedback.js')
  const feedback = useSessionFeedbackStore()
  let rating = 'like'
  api.getSessionFeedback = async () => ({ items: [{ run_id: 'run', rating }] })
  let completions = 0
  const firstView = runtime.observeThread('thread', { onTerminalDetected: () => completions++ })
  t.after(firstView)
  const current = { id: 'turn', status: 'completed', current_run_id: 'run', result_run_id: 'run' }
  api.getPublicThread = async () => ({ id: 'thread', yuxi: { current_turn: current } })
  const item = (n) => ({
    id: String(n), turn_id: n % 11 === 1 || n < 30 ? 'other' : 'turn',
    type: 'message', role: 'user', status: 'completed',
    content: [{ type: 'input_text', text: String(n) }], yuxi: { message_id: n }
  })
  const items = Array.from({ length: 410 }, (_, i) => item(410 - i))
  const final = { ...item(411), id: 'final', role: 'assistant', phase: 'final_answer',
    content: [{ type: 'output_text', text: '正式结果' }], yuxi: { message_id: 411, run_id: 'run' } }
  api.getThreadTurn = async () => ({ id: 'turn', status: 'completed',
    yuxi: { current_run_id: 'run', result_run_id: 'run', runs: [{ id: 'run', turn_id: 'turn', status: 'completed' }], output: [final] } })
  const calls = [], transferred = []
  let failTail = false
  api.getSessionItems = async (_threadId, { after, turnId, limit }) => {
    if (turnId && after && failTail) {
      failTail = false
      throw new Error('轮次尾页暂时不可用')
    }
    const source = turnId ? items.filter(item => item.turn_id === turnId) : items
    const offset = after ? source.findIndex(item => item.id === after) + 1 : 0
    assert.ok(!after || offset > 0, '游标必须属于被请求的范围')
    const data = source.slice(offset, offset + limit)
    calls.push([turnId ?? null, after ?? null])
    transferred.push(...data.map(item => item.id))
    return { data, has_more: offset + limit < source.length, last_id: data.at(-1)?.id, yuxi: { runs: [] } }
  }
  assert.equal(await runtime.loadThread('thread'), true)
  const firstWindow = items.slice(0, 300)
  const needed = items.filter((item, i) => i < 300 || item.turn_id === 'turn')
  const projected = () => Object.keys(runtime.getThreadState('thread').itemState.items).sort()
  assert.deepEqual(projected(), [...needed.map(i => i.id), 'final'].sort())
  assert.equal(runtime.threadMessages.thread.at(-1).content, '正式结果')
  assert.equal(feedback.getEntry('thread', 'run').record.rating, 'like')
  assert.deepEqual(transferred.slice().sort(), needed.map(i => i.id).sort(), '同一公开 item 首读只传一次')
  assert.deepEqual(calls, [[null, null], [null, '311'], [null, '211'], ['turn', '112']])
  assert.equal(runtime.historyPages.thread.after, firstWindow.at(-1).id)
  assert.equal(completions, 0, '打开历史不产生新的完成事件')
  await runtime.loadOlderHistory('thread')
  assert.deepEqual(projected(), [...items.map(i => i.id), 'final'].sort())
  assert.equal(runtime.historyPages.thread.hasMore, false)
  firstView()
  t.after(runtime.observeThread('thread', {}))
  rating = 'dislike'
  failTail = true
  await assert.rejects(runtime.loadThread('thread'), /轮次尾页暂时不可用/)
  await runtime.reconcileCooperationSessions([{ session_id: 'thread', turn_id: 'turn', turn_status: 'completed',
    current_run_id: 'run', result_run_id: 'run', run_status: 'completed' }])
  assert.equal(runtime.getThreadState('thread').historyLoaded, false, '单轮恢复不能替代会话分页')
  await runtime.loadThread('thread', { fresh: true })
  assert.equal(feedback.getEntry('thread', 'run').record.rating, 'dislike')
  assert.equal(runtime.historyPages.thread.after, '111')
  await runtime.loadOlderHistory('thread')
  assert.deepEqual(projected(), [...items.map(i => i.id), 'final'].sort())
})

test('首读历史期间有新输入且旧恢复在途，仍恢复唯一订阅', async (t) => {
  const { runtime, counts } = setup(t)
  t.after(runtime.observeThread('thread', {}))
  const done = { id: 'done', status: 'completed', current_run_id: 'old', result_run_id: 'old' }
  const next = { id: 'next', status: 'in_progress', current_run_id: 'new', result_run_id: null }
  let current = done
  const earlier = Promise.withResolvers()
  let hold = true
  api.getPublicThread = async () => {
    if (hold) {
      hold = false
      return earlier.promise
    }
    return { id: 'thread', yuxi: { current_turn: current } }
  }
  api.getThreadTurn = async (_threadId, id) => {
    const turn = id === done.id ? done : next
    return { id, status: turn.status, yuxi: { ...turn, output: [], runs: [] } }
  }
  const input = { id: 'new-input', turn_id: 'next', type: 'message', role: 'user', status: 'completed',
    content: [{ type: 'input_text', text: '外部提交的新输入' }], yuxi: { run_id: 'new', message_id: 2 } }
  api.getSessionItems = async () => {
    const data = current === next ? [input] : []
    current = next
    return { data, has_more: false, last_id: data.at(-1)?.id, yuxi: { runs: [] } }
  }
  const old = runtime.resumeActiveRunForThread('thread')
  await tick()
  const opening = runtime.loadThread('thread')
  await tick()
  earlier.resolve({ id: 'thread', yuxi: { current_turn: null } })
  await Promise.all([old, opening])
  assert.equal(counts().subscriptions, 1)
  assert.equal(runtime.getThreadState('thread').activeRunId, 'new')
  assert.deepEqual(runtime.threadMessages.thread.map(message => message.content), ['外部提交的新输入'])
})

for (const scenario of ['继续运行', '历史已经结束', '恢复期间结束']) {
  test(`首读收到运行摘要时，${scenario}只应用当前 Run 的结果`, async (t) => {
    const { runtime, counts } = setup(t)
    let completions = 0
    t.after(runtime.observeThread('thread', { onTerminalDetected: () => completions++ }))
    const settlesDuringRestore = scenario === '恢复期间结束'
    const running = scenario === '继续运行'
    const current = () => ({
      id: 'turn', status: completed ? 'completed' : 'in_progress', current_run_id: 'run',
      result_run_id: completed ? 'run' : null
    })
    const run = () => ({ id: 'run', turn_id: 'turn', status: completed ? 'completed' : 'running' })
    let completed = scenario === '历史已经结束'
    let turnReads = 0
    api.getPublicThread = async () => ({ id: 'thread', yuxi: { current_turn: current() } })
    api.getThreadTurn = async () => {
      if (++turnReads === 2 && settlesDuringRestore) completed = true
      return { id: 'turn', status: current().status, yuxi: { ...current(), output: [], runs: [run()] } }
    }
    let reads = 0
    const started = Promise.withResolvers(), gate = Promise.withResolvers()
    api.getSessionItems = async () => {
      reads++
      started.resolve()
      await gate.promise
      const data = [{ id: 'input', turn_id: 'turn', type: 'message', role: 'user', status: 'completed',
        content: [{ type: 'input_text', text: '持久输入' }], yuxi: { run_id: 'run', message_id: 1 } }]
      return { data, has_more: false, last_id: data.at(-1)?.id, yuxi: { runs: [run()] } }
    }
    const opening = runtime.loadThread('thread')
    await started.promise
    const summary = [{ session_id: 'thread', turn_id: 'turn', turn_status: 'running',
      current_run_id: scenario === '历史已经结束' ? 'old-run' : 'run', result_run_id: null, run_status: 'running' }]
    const syncing = runtime.reconcileCooperationSessions(summary)
    gate.resolve()
    await Promise.all([opening, syncing])
    assert.equal(counts().subscriptions, running ? 1 : 0)
    assert.deepEqual(runtime.threadMessages.thread.map(m => m.content), ['持久输入'])
    await runtime.reconcileCooperationSessions([{ ...summary[0], turn_status: running ? 'running' : 'completed',
      current_run_id: 'run', result_run_id: current().result_run_id, run_status: run().status }])
    assert.equal(reads, settlesDuringRestore ? 2 : 1)
    assert.equal(completions, settlesDuringRestore ? 1 : 0)
  })
}

for (const retry of ['手动重读', '在途协作摘要']) {
  test(`初读详情失败后由${retry}恢复新输入和运行`, async (t) => {
    const { runtime, counts } = setup(t)
    t.after(runtime.observeThread('thread', {}))
    const run = { id: 'run', turn_id: 'turn', status: 'running' }
    const current = { id: 'turn', status: 'in_progress', current_run_id: 'run' }
    const verifying = Promise.withResolvers()
    const verification = Promise.withResolvers()
    let detailReads = 0
    let submitted = false
    api.getPublicThread = async () => {
      if (++detailReads === 2) {
        verifying.resolve()
        await verification.promise
        throw new Error('连接中断')
      }
      return { id: 'thread', yuxi: { current_turn: submitted ? current : null } }
    }
    api.getThreadTurn = async () => ({ id: 'turn', status: current.status, yuxi: {
      ...current, output: [], runs: [run] } })
    const added = retry === '手动重读' ? 350 : 1
    api.getSessionItems = async (_threadId, { after, limit }) => {
      const first = after ? Number(after) - 1 : submitted ? added + 1 : 1
      const data = Array.from({ length: Math.min(first, limit) }, (_, index) => {
        const n = first - index
        return { id: String(n), turn_id: n === 1 ? 'old' : 'turn', type: 'message', role: 'user', status: 'completed',
          content: [{ type: 'input_text', text: `输入 ${n}` }], yuxi: { message_id: n, run_id: n === 1 ? 'old-run' : 'run' } }
      })
      return { data, last_id: data.at(-1)?.id, has_more: first > limit, yuxi: { runs: submitted ? [run] : [] } }
    }
    const opening = runtime.loadThread('thread')
    const failed = assert.rejects(opening, /连接中断/)
    await verifying.promise
    submitted = true
    const sync = retry === '在途协作摘要' && runtime.reconcileCooperationSessions([{
      session_id: 'thread', turn_id: 'turn', turn_status: 'running', current_run_id: 'run', run_status: 'running'
    }])
    verification.resolve()
    await failed
    await (sync || runtime.loadThread('thread', { fresh: true }))
    assert.equal(runtime.getThreadState('thread').activeRunId, 'run')
    assert.equal(counts().subscriptions, 1)
    assert.deepEqual(runtime.threadMessages.thread.map(message => message.content),
      Array.from({ length: added + 1 }, (_, index) => `输入 ${index + 1}`))
  })
}

test('首次会话页等待在途轮次页，保留会话范围的分页游标', async (t) => {
  const { runtime } = setup(t)
  t.after(runtime.observeThread('thread', {}))
  const current = { id: 'turn', status: 'completed', current_run_id: 'run', result_run_id: 'run' }
  api.getPublicThread = async () => ({ id: 'thread', yuxi: { current_turn: current } })
  api.getThreadTurn = async () => ({ id: 'turn', status: 'completed', yuxi: { ...current, output: [], runs: [] } })
  const started = Promise.withResolvers(), gate = Promise.withResolvers()
  api.getSessionItems = async (_id, { turnId }) => {
    started.resolve()
    await gate.promise
    const ids = turnId ? ['turn'] : ['turn', 'other']
    return { data: ids.map((id, index) => ({ id, turn_id: id, type: 'message', role: 'user', status: 'completed',
      content: [{ type: 'input_text', text: id }], yuxi: { message_id: 2 - index } })),
    last_id: ids.at(-1), has_more: false, yuxi: { runs: [] } }
  }
  const prior = runtime.reconcileTurnHistory('thread', 'turn')
  await started.promise
  const terminal = runtime.reconcileTurnHistory('thread', 'turn')
  await tick()
  const opening = runtime.loadThread('thread')
  await tick()
  gate.resolve()
  await Promise.all([prior, terminal, opening])
  assert.equal(runtime.getThreadState('thread').historyLoaded, true)
  assert.equal(runtime.historyPages.thread.after, 'other')
})

test('历史、流和完成后的显示由同一 item 集合派生，不依赖历史再同步', async (t) => {
  const h = setup(t)
  const detach = h.runtime.observeThread('thread', {})
  await h.runtime.refreshThreadHistory('thread')
  const stream = h.runtime.startRunStream('thread', 'run', null, { turnId: 'turn' })
  await tick()
  h.emit({ type: 'agent.session.turn.item.added', event_id: 'added', session_id: 'thread', turn_id: 'turn',
    item: { id: 'live', type: 'message', role: 'assistant', status: 'in_progress',
      content: [{ type: 'output_text', text: '' }], yuxi: { run_id: 'run', message_id: 1 } } })
  h.emit({ type: 'agent.session.turn.output_text.delta', event_id: 'delta', session_id: 'thread', turn_id: 'turn',
    item_id: 'live', content_index: 0, delta: 'one fact' })
  await tick()
  assert.equal(h.runtime.threadMessages.thread[0].content, 'one fact')
  const source = h.runtime.getThreadState('thread').itemState
  h.runtime.resetThreadExecution('thread', { preserveInputMonitors: true })
  assert.equal(h.runtime.getThreadState('thread').itemState, source)
  assert.equal(h.runtime.threadMessages.thread[0].content, 'one fact')
  detach()
  await stream
})

test('独立阅读装载共享历史与恢复；失败保持可重试而不是标成已就绪', async (t) => {
  const { runtime, counts } = setup(t)
  const left = runtime.observeThread('reading', {})
  const right = runtime.observeThread('reading', {})
  t.after(left)
  t.after(right)
  let reads = 0
  api.getSessionItems = async () => {
    reads++
    if (reads === 1) throw new Error('历史读取失败')
    return { data: [{ id: 'read', type: 'message', role: 'user', status: 'completed',
      content: [{ type: 'input_text', text: '持久历史' }], yuxi: {} }], yuxi: { runs: [] }, has_more: false }
  }
  const failed = await Promise.allSettled([runtime.loadThread('reading'), runtime.loadThread('reading')])
  assert.ok(failed.every((result) => result.status === 'rejected'))
  assert.equal(runtime.getThreadState('reading').readingReady, false)
  assert.equal(runtime.getThreadState('reading').readingError.message, '历史读取失败')
  assert.equal(reads, 1)
  assert.deepEqual(await Promise.all([runtime.loadThread('reading'), runtime.loadThread('reading')]), [true, true])
  assert.equal(runtime.threadMessages.reading[0].content, '持久历史')
  assert.equal(runtime.getThreadState('reading').readingError, null)
  assert.equal(counts().subscriptions, 1)
  assert.equal(reads, 2)
  left()
  assert.equal(counts().aborted, 0)
  right()
  assert.equal(counts().aborted, 1)
  assert.equal(runtime.threadMessages.reading, undefined)
})

test('旧身份的在途历史与恢复不能写回同名 Thread 的新身份', async (t) => {
  const { runtime } = setup(t)
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/index.js')
  const user = useUserStore()
  user.token = 'old-user'
  const releaseOld = runtime.observeThread('same', {})
  let resolveOld, started
  const pending = new Promise((resolve) => { started = resolve })
  const page = (text) => ({ data: [{ id: 'same-message', type: 'message', role: 'user', status: 'completed',
    content: [{ type: 'input_text', text }], yuxi: {} }], yuxi: { runs: [] }, has_more: false })
  api.getSessionItems = () => { started(); return new Promise((resolve) => { resolveOld = resolve }) }
  const oldRead = runtime.loadThread('same')
  await pending
  user.token = 'new-user'
  releaseOld()
  const releaseNew = runtime.observeThread('same', {})
  t.after(releaseNew)
  api.getSessionItems = async () => page('新身份历史')
  assert.equal(await runtime.loadThread('same'), true)
  resolveOld(page('旧身份历史'))
  assert.equal(await oldRead, false)
  assert.deepEqual(runtime.threadMessages.same.map((message) => message.content), ['新身份历史'])
})
