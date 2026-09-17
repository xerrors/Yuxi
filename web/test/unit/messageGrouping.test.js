import assert from 'node:assert/strict'
import test from 'node:test'
import {
  groupConversationContinuations,
  collapseConversationProcess,
  formatProcessDuration,
  isConversationSettled,
  formatEmptyRunStatus
} from '../../src/utils/conversationProcessGrouping.js'

test('关联续跑组成连续回答，消息归属不变且只累计执行耗时', () => {
  const groups = [
    {
      run: { run_id: 'a', timing: { total_latency_ms: 1000 } },
      status: 'finished',
      messages: [{ id: 'a1', run_id: 'a', type: 'ai', isLast: true }]
    },
    {
      run: {
        run_id: 'b',
        run_type: 'resume',
        created_by_run_id: 'a',
        timing: { total_latency_ms: 2000 }
      },
      status: 'finished',
      messages: [{ id: 'b1', run_id: 'b', type: 'ai', isLast: true }]
    },
    {
      run: {
        run_id: 'c',
        run_type: 'resume',
        created_by_run_id: 'b',
        timing: { total_latency_ms: 3000 }
      },
      status: 'streaming',
      messages: [{ id: 'c1', run_id: 'c', type: 'ai', isLast: true }]
    }
  ]
  const original = structuredClone(groups)
  const result = groupConversationContinuations(groups)
  assert.equal(result.length, 1)
  assert.deepEqual(
    result[0].messages.map((message) => message.run_id),
    ['a', 'b', 'c']
  )
  assert.equal(result[0].run, groups[2].run)
  assert.equal(result[0].status, 'streaming')
  assert.equal(result[0].displayKey, 'a')
  assert.equal(result[0].processTiming.total_latency_ms, 6000)
  assert.deepEqual(result[0].messages.map((message) => message.isLast), [false, false, true])
  assert.deepEqual(groups, original)
  groups[1].run.timing = null
  assert.equal(groupConversationContinuations(groups)[0].processTiming.total_latency_ms, null)
})

test('新用户、独立 Run、未知关系及零消息失败不并入前一回答', () => {
  const first = { run: { run_id: 'a' }, messages: [{ type: 'ai' }] }
  for (const next of [
    { run: { run_id: 'b', run_type: 'chat', created_by_run_id: 'a' }, messages: [{ type: 'ai' }] },
    {
      run: { run_id: 'b', run_type: 'resume', created_by_run_id: 'other' },
      messages: [{ type: 'ai' }]
    },
    { messages: [{ type: 'ai' }] },
    {
      run: { run_id: 'b', run_type: 'resume', created_by_run_id: 'a' },
      messages: [{ type: 'human' }, { type: 'ai' }]
    },
    {
      run: { run_id: 'b', run_type: 'resume', created_by_run_id: 'a', status: 'failed' },
      messages: []
    }
  ]) {
    assert.deepEqual(groupConversationContinuations([first, next]), [first, next])
  }
  const resume = { run: { run_id: 'b', run_type: 'resume', created_by_run_id: 'a' }, messages: [{ type: 'ai' }] }
  for (const previous of [{ messages: [{ type: 'ai' }] }, { run: { run_id: 'a', status: 'failed' }, messages: [] }]) {
    assert.deepEqual(groupConversationContinuations([previous, resume]), [previous, resume])
  }
})

test('已完成对话使用后端 Run 总耗时聚合过程组', () => {
  const items = collapseConversationProcess(
    [
      { key: 'h1', type: 'message', message: { type: 'human' } },
      { key: 'a1', type: 'message', message: { type: 'ai' } },
      { key: 'tools', type: 'tool-group', toolCalls: [{ id: 't1' }, { id: 't2' }] },
      {
        key: 'a3',
        type: 'message',
        message: {
          type: 'ai',
          run_started_at: '2026-08-20T00:00:00Z',
          run_finished_at: '2026-08-20T00:01:05Z',
          run_id: 'run-final'
        }
      }
    ],
    true,
    { total_latency_ms: 64000 }
  )
  assert.deepEqual(items.map((item) => item.type), ['message', 'process-group', 'message'])
  assert.equal(items[1].messageCount, 1)
  assert.equal(items[1].toolCallCount, 2)
  assert.equal(items[1].durationMs, 64000)
})

test('只有旧 Run 时间戳时不推算过程耗时', () => {
  const items = collapseConversationProcess(
    [
      { key: 'h1', type: 'message', message: { type: 'human' } },
      { key: 'a1', type: 'message', message: { type: 'ai' } },
      {
        key: 'a2',
        type: 'message',
        message: {
          type: 'ai',
          run_started_at: '2026-08-20T00:00:00Z',
          run_finished_at: '2026-08-20T00:01:05Z'
        }
      }
    ],
    true
  )

  assert.equal(items[1].durationMs, null)
  assert.equal(formatProcessDuration(items[1].durationMs), '处理过程')
})

test('运行中或最终消息后仍有工具调用时不聚合过程', () => {
  const items = [
    { key: 'h1', type: 'message', message: { type: 'human' } },
    { key: 'a1', type: 'message', message: { type: 'ai' } },
    { key: 'tools', type: 'tool-group', toolCalls: [{ id: 't1' }] }
  ]
  assert.equal(collapseConversationProcess(items).some((item) => item.type === 'process-group'), false)
  assert.equal(
    collapseConversationProcess(items, true).some((item) => item.type === 'process-group'),
    false
  )
})

test('formatProcessDuration: 复用 Run 时延格式', () => {
  assert.equal(formatProcessDuration(0), '耗时 0ms')
  assert.equal(formatProcessDuration(null), '处理过程')
  assert.equal(formatProcessDuration(undefined), '处理过程')
  assert.equal(formatProcessDuration(5000), '耗时 5.0s')
  assert.equal(formatProcessDuration(59000), '耗时 59s')
  assert.equal(formatProcessDuration(60000), '耗时 1m 0s')
  assert.equal(formatProcessDuration(65000), '耗时 1m 5s')
  assert.equal(formatProcessDuration(125000), '耗时 2m 5s')
})


test('零消息后续 Run 不隐藏上一条完成回答的操作栏，关联 resume 仍等待续写', () => {
  const answer = { run: { run_id: 'run-a', status: 'completed' }, messages: [{ type: 'human' }, { type: 'ai' }] }
  const empty = { run: { run_id: 'run-b', run_type: 'chat', status: 'failed' }, messages: [] }
  assert.equal(isConversationSettled([answer, empty], answer), true)
  const resume = { run: { run_id: 'resume', run_type: 'resume', created_by_run_id: 'run-a' }, messages: [] }
  assert.equal(isConversationSettled([answer, resume], answer), false)
  assert.equal(isConversationSettled([answer], answer, true), false)
  assert.equal(formatEmptyRunStatus(empty.run.status), '本次运行失败')
  assert.equal(formatEmptyRunStatus('cancelled'), '本次运行已取消')
})
