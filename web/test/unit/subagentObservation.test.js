import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { setImmediate } from 'node:timers'
import { createServer } from 'vite'
import { effectScope, nextTick, ref } from 'vue'

let server, agentApi, useSubagentRuns
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  server = await createServer({ server: { middlewareMode: true, hmr: false } })
  ;({ agentApi } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ useSubagentRuns } = await server.ssrLoadModule('/src/composables/useSubagentRuns.js'))
})
after(async () => {
  await server?.close()
  delete globalThis.localStorage
})
const settle = async () => {
  await nextTick()
  await new Promise((resolve) => setImmediate(resolve))
}

/** 用真实 SSE 字节驱动观察器，父 state 保持冻结。 */
function setup(t, initial) {
  const states = new Map(initial.map((run) => [run.run_id, run.status]))
  const streams = new Map()
  const requests = []
  const originalGet = agentApi.getAgentRun
  const originalStream = agentApi.streamAgentRunEvents
  agentApi.getAgentRun = async (id) => ({ run: { id, status: states.get(id) } })
  agentApi.streamAgentRunEvents = async (id, cursor, { signal }) => {
    requests.push({ id, cursor, signal })
    return new Response(
      new ReadableStream({
        start(controller) {
          streams.set(id, controller)
          signal.addEventListener(
            'abort',
            () => controller.error(new DOMException('aborted', 'AbortError')),
            { once: true }
          )
        }
      })
    )
  }
  const scope = effectScope()
  const thread = ref('user:parent')
  const runs = ref(initial)
  const enabled = ref(true)
  const observed = scope.run(() => useSubagentRuns({ scope: thread, runs, enabled }))
  t.after(() => {
    scope.stop()
    agentApi.getAgentRun = originalGet
    agentApi.streamAgentRunEvents = originalStream
  })
  const emit = (id, event, seq = '100-1') =>
    streams
      .get(id)
      .enqueue(
        new TextEncoder().encode(
          `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify({ run_id: id, payload: {} })}\n\n`
        )
      )
  return { states, streams, requests, thread, runs, enabled, observed, scope, emit }
}

test('父 await 的 state 冻结时，快子任务先完成，慢任务仍运行', async (t) => {
  const h = setup(t, [
    { run_id: 'fast', child_thread_id: 'a', status: 'running' },
    { run_id: 'slow', child_thread_id: 'b', status: 'running' }
  ])
  await settle()
  h.states.set('fast', 'completed')
  h.emit('fast', 'end')
  h.streams.get('fast').close()
  await settle()
  assert.deepEqual(
    h.observed.value.map((run) => run.status),
    ['completed', 'running']
  )
  assert.equal(h.runs.value[0].status, 'running', '没有借父 state 的刷新完成测试')
  assert.equal(h.requests[0].signal.aborted, true)
  assert.equal(h.requests[1].signal.aborted, false)
  h.runs.value = h.runs.value.map((run) => ({ ...run, status: 'pending' }))
  await settle()
  assert.deepEqual(
    h.observed.value.map((run) => run.status),
    ['completed', 'running']
  )
})

test('重新加载历史记录先回读数据库，已终态 Run 不建立订阅', async (t) => {
  const h = setup(t, [{ run_id: 'done', status: 'running' }])
  h.states.set('done', 'completed')
  // 首次查询已发出，模拟重新打开同一会话。
  await settle()
  h.thread.value = 'user:other'
  await settle()
  assert.equal(h.observed.value[0].status, 'completed')
  assert.equal(h.requests.length, 1)
})

test('切线程时关闭旧连接，延迟状态查询不能污染新会话', async (t) => {
  const h = setup(t, [{ run_id: 'old', status: 'running' }])
  await settle()
  let resolveOld
  agentApi.getAgentRun = (id) =>
    id === 'old'
      ? new Promise((resolve) => {
          resolveOld = resolve
        })
      : Promise.resolve({ run: { status: 'completed' } })
  h.emit('old', 'metadata')
  await settle()
  h.thread.value = 'user:new-thread'
  h.runs.value = [{ run_id: 'new', status: 'pending' }]
  await settle()
  resolveOld({ run: { status: 'failed' } })
  await settle()
  assert.equal(h.requests[0].signal.aborted, true)
  assert.deepEqual(
    h.observed.value.map((run) => run.run_id),
    ['new']
  )
  assert.equal(h.observed.value[0].status, 'completed')
})

test('断流回读当前状态并携带子 Run 自己的游标重连', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  const h = setup(t, [{ run_id: 'child', status: 'running' }])
  await settle()
  h.emit('child', 'messages', '200-3')
  h.streams.get('child').close()
  await settle()
  t.mock.timers.tick(2000)
  await settle()
  assert.equal(h.requests.length, 2)
  assert.equal(h.requests[1].cursor, '200-3')
  h.scope.stop()
  assert.equal(h.requests[1].signal.aborted, true)
})

test('连接无事件时回读终态，退出页面后不再轮询', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  const h = setup(t, [{ run_id: 'child', status: 'running' }])
  await settle()
  h.states.set('child', 'cancelled')
  t.mock.timers.tick(15000)
  await settle()
  assert.equal(h.observed.value[0].status, 'cancelled')
  assert.equal(h.requests[0].signal.aborted, true)
  h.enabled.value = false
  await settle()
  assert.deepEqual(h.observed.value, [])
})

test('HTTP 故障明确显示未知状态并在重试后恢复', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  const h = setup(t, [{ run_id: 'child', status: 'running' }])
  await settle()
  agentApi.getAgentRun = async () => {
    throw new Error('offline')
  }
  h.streams.get('child').error(new Error('offline'))
  await settle()
  assert.equal(h.observed.value[0].observation_error, true)
  agentApi.getAgentRun = async () => ({ run: { status: 'completed' } })
  t.mock.timers.tick(2000)
  await settle()
  assert.equal(h.observed.value[0].status, 'completed')
  assert.equal(h.observed.value[0].observation_error, false)
})

test('同子线程旧 Run 晚发现时仍按创建顺序展示最新 Run', async (t) => {
  const h = setup(t, [
    {
      run_id: 'new',
      child_thread_id: 'same',
      status: 'running',
      created_at: '2026-09-17T02:00:00Z'
    }
  ])
  await settle()
  h.states.set('old', 'completed')
  h.runs.value = [
    ...h.runs.value,
    {
      run_id: 'old',
      child_thread_id: 'same',
      status: 'completed',
      created_at: '2026-09-17T01:00:00Z'
    }
  ]
  await settle()
  const { mergeSubagentRunsForDisplay } = await import('../../src/utils/subagentRuns.js')
  assert.deepEqual(
    mergeSubagentRunsForDisplay(h.observed.value).map((run) => run.run_id),
    ['new']
  )
})

test('八个活跃子任务最多占用三个 SSE，剩余任务仍可回读完成并补位', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  const h = setup(
    t,
    Array.from({ length: 8 }, (_, index) => ({ run_id: `child-${index}`, status: 'running' }))
  )
  await settle()
  assert.equal(h.requests.filter((item) => !item.signal.aborted).length, 3)
  h.states.set('child-7', 'completed')
  t.mock.timers.tick(2000)
  await settle()
  assert.equal(h.observed.value.find((run) => run.run_id === 'child-7').status, 'completed')
  assert.equal(h.requests.length, 3)
  h.states.set('child-0', 'completed')
  h.emit('child-0', 'end')
  h.streams.get('child-0').close()
  await settle()
  t.mock.timers.tick(2000)
  await settle()
  assert.equal(h.requests.length, 4)
  assert.equal(h.requests.filter((item) => !item.signal.aborted).length, 3)
})

test('切换会话取消在途 HTTP 状态查询，释放请求连接', async (t) => {
  const h = setup(t, [{ run_id: 'old', status: 'running' }])
  await settle()
  let signal, resolveRequest
  agentApi.getAgentRun = (_id, options) => {
    signal = options?.signal
    return new Promise((resolve) => {
      resolveRequest = resolve
    })
  }
  h.emit('old', 'metadata')
  await settle()
  const oldSignal = signal
  h.runs.value = []
  h.thread.value = 'user:other'
  await settle()
  resolveRequest({ run: { status: 'completed' } })
  assert.equal(oldSignal?.aborted, true)
})
