import assert from 'node:assert/strict'
import { before, after, test } from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'
import { createItemState } from '../../src/modules/session/model/agentItems.js'

let server, api, resumeSessionWaitpoint, createSessionInput
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  ;({ agentApi: api } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ resumeSessionWaitpoint } = await server.ssrLoadModule(
    '/src/modules/session/model/sessionCommands.js'
  ))
  ;({ createSessionInput } = await server.ssrLoadModule('/src/modules/session/model/sessionInput.js'))
})
after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

const waitingTurn = () => ({
  status: 'requires_action',
  yuxi: {
    current_run_id: 'run-1',
    waitpoint: { id: 'wait-1', run_id: 'run-1', questions: [{ question_id: 'q1' }, { question_id: 'q2' }] }
  }
})
const answerCommand = () => ({
  threadId: 'thread-1', turnId: 'turn-1', runId: 'run-1', kind: 'question', answer: { q2: '二', q1: '一' }
})

test('问答按当前问题ID构造协议，恢复结果绑定同一Turn和等待点', async (t) => {
  const writes = []
  t.mock.method(api, 'getThreadTurn', async (thread, turn) => {
    assert.deepEqual([thread, turn], ['thread-1', 'turn-1'])
    return waitingTurn()
  })
  t.mock.method(api, 'resumeThreadTurn', async (thread, body) => {
    writes.push({ thread, body })
    return { run_id: 'run-2' }
  })
  assert.deepEqual(await resumeSessionWaitpoint(answerCommand()), { run_id: 'run-2' })
  assert.deepEqual(writes, [{ thread: 'thread-1', body: {
    turn_id: 'turn-1', waitpoint_id: 'wait-1', idempotency_key: 'resume-wait-1',
    response: { type: 'answer', answers: [{ question_id: 'q1', answer: '一' }, { question_id: 'q2', answer: '二' }] }
  } }])
})

test('审批使用持久call_id并保留每项允许或拒绝', async (t) => {
  const turn = waitingTurn()
  turn.yuxi.waitpoint.calls = [{ call_id: 'call-a' }, { call_id: 'call-b' }]
  t.mock.method(api, 'getThreadTurn', async () => turn)
  let response
  t.mock.method(api, 'resumeThreadTurn', async (_, body) => { response = body.response; return { run_id: 'run-2' } })
  await resumeSessionWaitpoint({ ...answerCommand(), kind: 'tool_approval', answer: { decisions: [{ type: 'approve' }, { type: 'reject' }] } })
  assert.deepEqual(response, { type: 'approval', decisions: [
    { call_id: 'call-a', decision: 'approve' }, { call_id: 'call-b', decision: 'reject' }
  ] })
})

for (const defect of ['finished', 'different-run', 'different-waitpoint-run', 'missing-waitpoint', 'missing-answer', 'missing-turn', 'changed-calls']) {
  test(`拒绝过期或不完整等待点：${defect}`, async (t) => {
    const turn = waitingTurn(), command = answerCommand()
    if (defect === 'finished') turn.status = 'completed'
    if (defect === 'different-run') turn.yuxi.current_run_id = 'run-other'
    if (defect === 'different-waitpoint-run') turn.yuxi.waitpoint.run_id = 'run-other'
    if (defect === 'missing-waitpoint') delete turn.yuxi.waitpoint
    if (defect === 'missing-answer') delete command.answer.q2
    if (defect === 'missing-turn') command.turnId = null
    if (defect === 'changed-calls') {
      command.kind = 'tool_approval'
      turn.yuxi.waitpoint.calls = [{ call_id: 'call-a' }]
      command.answer = { decisions: [] }
    }
    t.mock.method(api, 'getThreadTurn', async () => turn)
    const writes = []
    t.mock.method(api, 'resumeThreadTurn', async (_, body) => { writes.push(body); return { run_id: 'run-2' } })
    await assert.rejects(resumeSessionWaitpoint(command), /等待中的 Turn|已变化|回答全部问题/)
    assert.deepEqual(writes, [])
  })
}

test('恢复响应没有Run时不能开始观察相邻执行', async (t) => {
  t.mock.method(api, 'getThreadTurn', async () => waitingTurn())
  t.mock.method(api, 'resumeThreadTurn', async () => ({}))
  await assert.rejects(resumeSessionWaitpoint(answerCommand()), /未创建 Run/)
})

function harness(t, active = false) {
  const state = { activeRunId: active ? 'existing' : null, isStreaming: active,
    queuedInputs: [], optimisticInputs: {}, itemState: createItemState() }
  const runtime = { pendingSends: {}, getThreadState: () => state, isCurrentThreadState: (_id, candidate) => candidate === state,
    resetThreadExecution: () => { state.optimisticInputs = {} },
    startInputMonitor() {} }
  const accepted = [], rejected = []
  const controller = createSessionInput({ runtime,
    onPrepared() {}, onAccepted: (_id, draft) => accepted.push(draft),
    onRejected: (_id, draft, error, result) => rejected.push({ draft, error, ...result }) })
  const draft = { text: '原始消息', images: [], attachments: [{ id: 'file' }], modelSpec: 'model:one',
    toolApprovalMode: 'default', mode: 'follow_up', createdFromDraft: false }
  return { state, runtime, controller, draft, accepted, rejected }
}

test('接收后消费冻结草稿并交给唯一输入观察者，保留已知历史', async (t) => {
  const h = harness(t)
  h.state.itemState.items.old = { id: 'old' }
  t.mock.method(api, 'sendThreadMessage', async (_id, data) => {
    assert.equal(data.model_spec, 'model:one')
    assert.deepEqual(data.attachment_file_ids, ['file'])
    return { input_id: 'accepted' }
  })
  let monitored
  h.runtime.startInputMonitor = (_id, inputId) => { monitored = inputId }
  t.mock.method(api, 'getThreadInput', () => { throw Error('只有队列观察者可以读取输入') })
  assert.equal(await h.controller.send('thread', h.draft), true)
  assert.equal(monitored, 'accepted')
  assert.deepEqual(h.state.itemState.items.old, { id: 'old' })
  assert.equal(h.state.optimisticInputs.accepted[0].content, '原始消息', '回执到消费之间用户消息保持可见')
  assert.equal(h.state.optimisticInputs.accepted[0].delivery_status, 'queued')
  assert.equal(h.state.optimisticInputs.accepted[0].extra_metadata.input_id, 'accepted')
  assert.deepEqual(Object.keys(h.state.itemState.items), ['old'], '接收展示不能伪造正式历史')
  assert.equal(h.runtime.pendingSends.thread, undefined)
  assert.equal(h.accepted[0].text, '原始消息')
})

for (const receiptExists of [true, false]) {
  test(`未知接收结果重试${receiptExists ? '恢复回执' : '同载荷重发'}，忽略后来编辑的草稿`, async (t) => {
    const h = harness(t)
    h.draft.text = '@mcp:first @skill:ignored @mcp:second @mcp:first'
    let frozen
    t.mock.method(api, 'sendThreadMessage', async (_id, data) => {
      if (!frozen) { frozen = structuredClone(data); throw Object.assign(Error('offline'), { status: 503 }) }
      assert.deepEqual(data, frozen)
      return { input_id: 'accepted' }
    })
    assert.equal(await h.controller.send('thread', h.draft), false)
    assert.deepEqual(frozen.mcps, ['first', 'second'])
    h.draft.attachments.push({ id: 'later-file' })
    h.draft.text = '新草稿'
    let preparations = 0
    const originalController = createSessionInput({ runtime: h.runtime, onPrepared: () => preparations++, onAccepted: (_id, draft) => h.accepted.push(draft) })
    t.mock.method(api, 'getSessionReceipt', async (_id, key) => {
      assert.equal(key, frozen.idempotency_key)
      if (!receiptExists) throw Object.assign(Error('not found'), { status: 404 })
      return { input_id: 'accepted' }
    })
    if (receiptExists) t.mock.method(api, 'sendThreadMessage', () => { throw Error('不能重复提交') })
    assert.equal(await originalController.send('thread', h.draft, { retry: true }), true)
    assert.equal(preparations, 0, '重试不能再次消费编辑器当前草稿')
    assert.equal(h.accepted[0].text, '@mcp:first @skill:ignored @mcp:second @mcp:first')
    assert.deepEqual(h.accepted[0].data.mcps, ['first', 'second'])
    assert.deepEqual(h.accepted[0].attachments, [{ id: 'file' }])
  })
}

test('协作等待追加输入只进入队列，不重置运行流', async (t) => {
  const h = harness(t, true)
  h.runtime.resetThreadExecution = () => { throw Error('不能关闭协作等待流') }
  t.mock.method(api, 'sendThreadMessage', async () => ({ input_id: 'queued' }))
  assert.equal(await h.controller.send('thread', h.draft), true)
  assert.equal(h.state.queuedInputs[0].input_id, 'queued')
  assert.equal(h.state.activeRunId, 'existing')
  assert.equal(h.state.isStreaming, true)
  assert.deepEqual(h.state.optimisticInputs, {}, '运行中的后续输入继续只显示在队列')
})

test('明确拒绝才恢复草稿，回执查询失败不重发', async (t) => {
  const h = harness(t)
  t.mock.method(api, 'sendThreadMessage', async () => { throw Object.assign(Error('rejected'), { status: 422 }) })
  await h.controller.send('thread', h.draft)
  assert.equal(h.rejected[0].restore, true)
  assert.equal(h.runtime.pendingSends.thread, undefined)
  t.mock.method(api, 'sendThreadMessage', async () => { throw Error('unknown') })
  await h.controller.send('thread', h.draft)
  t.mock.method(api, 'getSessionReceipt', async () => { throw Object.assign(Error('unavailable'), { status: 503 }) })
  t.mock.method(api, 'sendThreadMessage', () => { throw Error('不能重发未知结果') })
  await h.controller.send('thread', h.draft, { retry: true })
  assert.equal(h.rejected.at(-1).error.message, 'unavailable')
  assert.equal(h.rejected.at(-1).restore, false)
})

test('观察者开始新 Run 后，接收流程不能再用旧快照覆盖运行状态', async (t) => {
  const h = harness(t)
  t.mock.method(api, 'sendThreadMessage', async () => ({ input_id: 'accepted' }))
  t.mock.method(api, 'getThreadInput', () => { throw Error('提交流程不得重复读取快照') })
  h.runtime.startInputMonitor = () => {
    h.state.activeRunId = 'already-running'
    h.state.isStreaming = true
  }
  assert.equal(await h.controller.send('thread', h.draft), true)
  assert.equal(h.state.activeRunId, 'already-running')
  assert.equal(h.state.isStreaming, true)
  assert.equal(h.rejected.length, 0)
})

test('首次发送缺失 Input 身份时保留未知请求，禁止消费草稿', async (t) => {
  const h = harness(t)
  t.mock.method(api, 'sendThreadMessage', async () => ({}))
  assert.equal(await h.controller.send('thread', h.draft), false)
  assert.match(h.rejected[0].error.message, /未返回 input_id/)
  assert.equal(h.rejected[0].restore, false)
  assert.equal(h.accepted.length, 0)
})

test('请求仍在发送时另一个视图也不能并发重试同一 Input', async (t) => {
  const h = harness(t)
  let release
  t.mock.method(api, 'sendThreadMessage', () => new Promise((resolve) => { release = resolve }))
  const sending = h.controller.send('thread', h.draft)
  assert.equal(h.runtime.pendingSends.thread.submitting, true)
  const other = createSessionInput({ runtime: h.runtime })
  assert.equal(await other.send('thread', h.draft, { retry: true }), false)
  release({ input_id: 'accepted' })
  assert.equal(await sending, true)
  assert.equal(h.accepted.length, 1)
})
