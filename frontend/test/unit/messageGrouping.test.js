import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'
import MessageProcessor from '../../src/modules/session/model/messageProcessor.js'
import {
  groupRunContinuations,
  collapseRunProcess,
  formatProcessDuration,
  isRunGroupSettled,
  formatEmptyRunStatus
} from '../../src/modules/session/model/runProcessGrouping.js'

let server, getMessageGroupDisplayItems
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  ;({ getMessageGroupDisplayItems } = await server.ssrLoadModule(
    '/src/modules/session/model/messageGrouping.js'
  ))
})
after(async () => { await server?.close() })

test('关联续跑组成连续回答，消息归属不变且只累计执行耗时', () => {
  const groups = [
    {
      run: { id: 'a', turn_id: 'turn-1', timing: { total_latency_ms: 1000 } },
      status: 'finished',
      messages: [{ id: 'a1', run_id: 'a', type: 'ai', isLast: true }]
    },
    {
      run: {
        id: 'b',
        turn_id: 'turn-1',
        run_type: 'resume',
        timing: { total_latency_ms: 2000 }
      },
      status: 'finished',
      messages: [{ id: 'b1', run_id: 'b', type: 'ai', isLast: true }]
    },
    {
      run: {
        id: 'c',
        turn_id: 'turn-1',
        run_type: 'resume',
        timing: { total_latency_ms: 3000 }
      },
      status: 'streaming',
      messages: [{ id: 'c1', run_id: 'c', type: 'ai', isLast: true }]
    }
  ]
  const original = structuredClone(groups)
  const result = groupRunContinuations(groups)
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
  assert.equal(groupRunContinuations(groups)[0].processTiming.total_latency_ms, null)
})

test('新用户、独立 Turn、未知归属及零消息失败不并入前一回答', () => {
  const first = { run: { id: 'a', turn_id: 'turn-1' }, messages: [{ type: 'ai' }] }
  for (const next of [
    { run: { id: 'b', turn_id: 'turn-1', run_type: 'chat' }, messages: [{ type: 'ai' }] },
    {
      run: { id: 'b', turn_id: 'turn-2', run_type: 'resume', created_by_run_id: 'a' },
      messages: [{ type: 'ai' }]
    },
    { run: { id: 'b', run_type: 'resume' }, messages: [{ type: 'ai' }] },
    { messages: [{ type: 'ai' }] },
    {
      run: { id: 'b', turn_id: 'turn-1', run_type: 'resume' },
      messages: [{ type: 'human' }, { type: 'ai' }]
    },
    {
      run: { id: 'b', turn_id: 'turn-1', run_type: 'resume', status: 'failed' },
      messages: []
    }
  ]) {
    assert.deepEqual(groupRunContinuations([first, next]), [first, next])
  }
  const resume = { run: { id: 'b', turn_id: 'turn-1', run_type: 'resume' }, messages: [{ type: 'ai' }] }
  for (const previous of [{ messages: [{ type: 'ai' }] }, { run: { id: 'a', status: 'failed' }, messages: [] }]) {
    assert.deepEqual(groupRunContinuations([previous, resume]), [previous, resume])
  }
})

test('已完成对话使用后端 Run 总耗时聚合过程组', () => {
  const items = collapseRunProcess(
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
  const items = collapseRunProcess(
    [
      { key: 'h1', type: 'message', message: { type: 'human' } },
      { key: 'a1', type: 'message', message: { type: 'ai' } },
      { key: 'thinking', type: 'tool-group', toolCalls: [], entries: [{ type: 'reasoning', content: '检查结果' }] },
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
  assert.equal(collapseRunProcess(items).some((item) => item.type === 'process-group'), false)
  assert.equal(
    collapseRunProcess(items, true).some((item) => item.type === 'process-group'),
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
  const answer = { run: { id: 'run-a', turn_id: 'turn-1', status: 'completed' }, messages: [{ type: 'human' }, { type: 'ai' }] }
  const empty = { run: { id: 'run-b', run_type: 'chat', status: 'failed' }, messages: [] }
  assert.equal(isRunGroupSettled([answer, empty], answer), true)
  const resume = { run: { id: 'resume', turn_id: 'turn-1', run_type: 'resume' }, messages: [] }
  assert.equal(isRunGroupSettled([answer, resume], answer), false)
  assert.equal(isRunGroupSettled([
    answer, { ...resume, run: { ...resume.run, turn_id: 'turn-2', created_by_run_id: 'run-a' } }
  ], answer), true)
  assert.equal(isRunGroupSettled([answer], answer, true), false)
  assert.equal(formatEmptyRunStatus(empty.run.status), '本次运行失败')
  assert.equal(formatEmptyRunStatus('cancelled'), '本次运行已取消')
  assert.equal(formatEmptyRunStatus('cancel_requested'), '取消中')
})

test('审批前后的工具和思考跨关联 Run 连续展示，正文仍独立', () => {
  const runs = [
    { id: 'first', turn_id: 'turn-1', status: 'interrupted', timing: { created_at: '2026-09-16T00:00:00Z' } },
    {
      id: 'resume',
      turn_id: 'turn-1',
      run_type: 'resume',
      status: 'completed',
      timing: { created_at: '2026-09-16T00:01:00Z' }
    }
  ]
  const history = [
    { id: 'human', run_id: 'first', type: 'human', content: '完成任务' },
    { id: 'm1', run_id: 'first', type: 'ai', tool_calls: [{ id: 't1', name: 'ls', args: {} }] },
    {
      id: 'm2',
      run_id: 'first',
      type: 'ai',
      reasoning_content: 'thinking1',
      tool_calls: [
        { id: 't2', name: 'read_file', args: {} },
        { id: 't3', name: 'ask_user_question', args: {}, status: 'success' }
      ]
    },
    {
      id: 'answer',
      run_id: 'resume',
      type: 'human',
      content: '{"type":"approval","decisions":[{"call_id":"t3","decision":"approve"}]}',
      message_type: 'resume'
    },
    {
      id: 'm3',
      run_id: 'resume',
      type: 'ai',
      reasoning_content: 'thinking2',
      tool_calls: [{ id: 't4', name: 'ls', args: {} }]
    },
    { id: 'm4', run_id: 'resume', type: 'ai', content: '最终回答' }
  ]
  const runGroups = MessageProcessor.convertServerHistoryToMessages(history, runs)
  const groups = groupRunContinuations(runGroups)
  assert.equal(groups.length, 1)
  const items = getMessageGroupDisplayItems(groups[0])
  assert.deepEqual(
    items.map((item) => item.type),
    ['message', 'tool-group', 'message']
  )
  assert.deepEqual(
    items[1].entries.map((entry) => (entry.type === 'tool' ? entry.toolCall.id : entry.content)),
    ['t1', 'thinking1', 't2', 't3', 'thinking2', 't4']
  )
  assert.equal(items[2].message.content, '最终回答')
  assert.equal(runGroups.length, 2)
  const streaming = groupRunContinuations([
    runGroups[0],
    { ...runGroups[1], status: 'streaming', messages: runGroups[1].messages.slice(0, 1) }
  ])
  assert.equal(streaming[0].status, 'streaming')
  assert.equal(getMessageGroupDisplayItems(streaming[0])[1].key, items[1].key)
})

test('协作恢复的实时消息没有 Run 快照时，仍与等待工具连续展示', () => {
  const waiting = {
    run: { id: 'before', turn_id: 'turn-1', status: 'interrupted' },
    messages: [{
      id: 'wait', type: 'ai', run_id: 'before', turn_id: 'turn-1',
      tool_calls: [{ id: 'wait-call', name: 'wait_inputs', status: 'waiting', args: {} }]
    }]
  }
  const live = {
    status: 'streaming',
    messages: [{
      id: 'thinking', type: 'ai', run_id: 'after', turn_id: 'turn-1',
      reasoning_content: '继续核验'
    }]
  }
  const groups = groupRunContinuations([waiting, live])
  assert.equal(groups.length, 1)
  const items = getMessageGroupDisplayItems(groups[0])
  assert.equal(items.length, 1)
  assert.deepEqual(items[0].entries.map((entry) => entry.type), ['tool', 'reasoning'])
  assert.equal(items[0].toolCalls[0].status, 'waiting')
  assert.deepEqual(groups[0].messages.map((message) => message.run_id), ['before', 'after'])
  assert.equal(groups[0].run.id, 'after')
  assert.equal(groups[0].status, 'streaming')
  for (const messages of [
    [{ ...live.messages[0], turn_id: 'turn-2' }],
    [{ ...live.messages[0], turn_id: undefined }],
    [{ id: 'user', type: 'human', content: '继续' }, ...live.messages]
  ]) {
    assert.equal(groupRunContinuations([waiting, { ...live, messages }]).length, 2)
  }
})

test('thinking 与相邻工具按顺序合并，正文和错误仍独立显示', () => {
  const messages = [
    { id: 'a1', type: 'ai', reasoning_content: '先检查', tool_calls: [{ id: 't1', name: 'ls', args: {} }] },
    { id: 'a2', type: 'ai', reasoning_content: '再确认', tool_calls: [{ id: 't2', name: 'read_file', args: {} }] },
    { id: 'a3', type: 'ai', reasoning_content: '得到结论', content: '最终回答' }
  ]
  const items = getMessageGroupDisplayItems({ messages })
  assert.deepEqual(items.map((item) => item.type), ['tool-group', 'message'])
  assert.deepEqual(items[0].entries.map((entry) => entry.type), ['reasoning', 'tool', 'reasoning', 'tool', 'reasoning'])
  assert.deepEqual(items[0].entries.filter((entry) => entry.type === 'reasoning').map((entry) => entry.content), ['先检查', '再确认', '得到结论'])
  assert.equal(items[0].toolCalls.length, 2)
  assert.equal(items[1].message.content, '最终回答')
  assert.equal(items[1].message.reasoning_content, '')
  assert.equal(messages[2].reasoning_content, '得到结论')

  const thinkingOnly = getMessageGroupDisplayItems({ messages: [{ id: 'a', type: 'ai', reasoning_content: '思考中' }] })
  assert.equal(thinkingOnly.length, 1)
  assert.equal(thinkingOnly[0].type, 'tool-group')
  assert.equal(thinkingOnly[0].entries[0].content, '思考中')
  assert.deepEqual(thinkingOnly[0].toolCalls, [])
  assert.deepEqual(getMessageGroupDisplayItems({ messages: [{ type: 'ai', content: '' }] }), [])

  const failed = getMessageGroupDisplayItems({ messages: [{ type: 'ai', reasoning_content: '检查中断', error_type: 'interrupted' }] })
  assert.deepEqual(failed.map((item) => item.type), ['tool-group', 'message'])
  assert.equal(failed[1].message.error_type, 'interrupted')
})

test('同一消息的 thinking、正文和工具分段使用不同的稳定 key', () => {
  const message = { id: 'mixed', type: 'ai', reasoning_content: '计划', content: '先查文件', tool_calls: [{ id: 't1', name: 'ls', args: {} }] }
  const items = getMessageGroupDisplayItems({ messages: [message] })
  assert.deepEqual(items.map((item) => item.type), ['tool-group', 'message', 'tool-group'])
  assert.equal(new Set(items.map((item) => item.key)).size, 3)
  assert.equal(items[0].entries[0].content, '计划')
  assert.equal(items[2].entries[0].toolCall.id, 't1')
  const updated = getMessageGroupDisplayItems({ messages: [{ ...message, content: '先查文件，再整理' }] })
  assert.deepEqual(updated.map((item) => item.key), items.map((item) => item.key))
})

test('交付物展示不把最后可见工具后的正文收入过程区', () => {
  for (const artifactTool of [
    { name: 'present_artifacts', args: { filepaths: ['/dog.png'] }, status: 'success' },
    { function: { name: 'present_artifacts', arguments: '{"filepaths":["/dog.png"]}' }, status: 'success' }
  ]) {
    const messages = [
      { id: 'human', type: 'human', content: '生成小狗图片' },
      { id: 'prepare', type: 'ai', content: '检查图片文件', tool_calls: [{ name: 'ls', args: {} }] },
      { id: 'answer', type: 'ai', content: '图片已生成，这是图片说明。', tool_calls: [artifactTool] },
      { id: 'confirm', type: 'ai', content: '图片已展示，可下载。' }
    ]
    const original = structuredClone(messages)
    const items = getMessageGroupDisplayItems({ messages }, { collapseIntermediate: true })
    assert.deepEqual(items.map(item => item.type), ['message', 'process-group', 'message', 'message'])
    assert.deepEqual(items[1].items.map(item => item.key), ['prepare', 'tool-group-prepare-tools'])
    assert.equal(items[1].toolCallCount, 1)
    assert.deepEqual(items.slice(2).map(item => item.message.id), ['answer', 'confirm'])
    assert.deepEqual(MessageProcessor.extractArtifactsFromMessageGroup({ messages }), ['/dog.png'])
    assert.deepEqual(messages, original)
  }
})

test('只有交付物展示时正文全部保留，混合调用或后续执行工具仍参与过程折叠', () => {
  const present = { name: 'present_artifacts', args: { filepaths: ['/dog.png'] }, status: 'success' }
  const execute = { name: 'bash', args: { command: 'check' } }
  const answer = { id: 'answer', type: 'ai', content: '图片说明', tool_calls: [present] }
  const confirm = { id: 'confirm', type: 'ai', content: '展示完成' }
  const human = { id: 'human', type: 'human', content: '生成图片' }
  for (const messages of [[human, answer], [human, answer, confirm]]) {
    const items = getMessageGroupDisplayItems({ messages }, { collapseIntermediate: true })
    assert.deepEqual(items.map(item => item.message.id), messages.map(message => message.id))
  }
  for (const messages of [
    [{ ...answer, tool_calls: [present, execute] }, confirm],
    [answer, { id: 'check', type: 'ai', content: '继续检查', tool_calls: [execute] }, confirm]
  ]) {
    const items = getMessageGroupDisplayItems({ messages }, { collapseIntermediate: true })
    assert.deepEqual(items.map(item => item.type), ['process-group', 'message'])
    assert.equal(items[0].toolCallCount, 1)
    assert.equal(items[0].items.at(-1).toolCalls[0].name, 'bash')
    assert.equal(items[1].message.id, 'confirm')
    const live = getMessageGroupDisplayItems({ messages })
    assert.equal(live.some(item => item.type === 'process-group'), false)
  }
})
