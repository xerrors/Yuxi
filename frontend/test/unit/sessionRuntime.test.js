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
  await h.runtime.fetchThreadMessages({ threadId: 'thread', fresh: true })
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
    runtime.fetchThreadMessages({ threadId: 'thread' }),
    runtime.fetchThreadMessages({ threadId: 'thread' })
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
  const history = runtime.fetchThreadMessages({ threadId: 'thread' })
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
    const old = runtime.fetchThreadMessages({ threadId: 'thread' })
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
  await runtime.fetchThreadMessages({ threadId: 'thread' })
  assert.deepEqual(
    runtime.threadMessages.thread.map((message) => message.content),
    Array.from({ length: 6 }, (_, index) => `message ${index + 4}`)
  )
  assert.equal(runtime.historyPages.thread.hasMore, true)
  await runtime.fetchThreadMessages({ threadId: 'thread', more: true })
  assert.equal(runtime.threadMessages.thread[0].content, 'message 1')
  latest = 17
  await runtime.fetchThreadMessages({ threadId: 'thread', fresh: true })
  assert.deepEqual(
    runtime.threadMessages.thread.map((message) => message.content),
    Array.from({ length: 17 }, (_, index) => `message ${index + 1}`)
  )
  assert.deepEqual(calls, [undefined, '8', '6', '4', '2', undefined, '16', '14', '12', '10'])
  detach()
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
  await runtime.fetchThreadMessages({ threadId: 'thread' })
  assert.equal(runtime.threadMessages.thread.length, 300)
  assert.equal(runtime.threadMessages.thread[0].content, 'message 351')
  assert.equal(runtime.historyPages.thread.after, '351')
  await Promise.all([
    runtime.fetchThreadMessages({ threadId: 'thread', more: true }),
    runtime.fetchThreadMessages({ threadId: 'thread', more: true })
  ])
  assert.equal(runtime.threadMessages.thread.length, 600)
  assert.equal(runtime.historyPages.thread.after, '51')
  await runtime.fetchThreadMessages({ threadId: 'thread', more: true })
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

test('历史、流和完成后的显示由同一 item 集合派生，不依赖历史再同步', async (t) => {
  const h = setup(t)
  const detach = h.runtime.observeThread('thread', {})
  await h.runtime.fetchThreadMessages({ threadId: 'thread' })
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
