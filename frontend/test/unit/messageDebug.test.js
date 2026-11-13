import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  bindMessageInputRun,
  buildMessageDebugTraceSpans,
  buildMessageDebugEntries,
  buildMessageDebugTurnData,
  constrainMessageDebugInspectorHeight,
  constrainMessageDebugInspectorWidth,
  extractMessageToolNames,
  formatAuditDuration,
  formatMessageDebugContent,
  getMessageInputId,
  getMessageRunId,
  groupMessageDebugEntries,
  getMessageDebugEntryTimeRange,
  isMessageDebugEntryInTimeRange,
  isMessageDebugTimelineMarkSelected,
  mergeMessageDebugRunGroups,
  serializeMessageDebugTurn,
} from '../../src/modules/session/model/messageDebug.js'
import { resolveLangfuseTraceUrl } from '../../src/shared/lib/langfuse.js'

test('时间概览只高亮当前选中记录，选中 Turn 时高亮其全部时间条', () => {
  assert.equal(isMessageDebugTimelineMarkSelected('turn:run-a-0', 'run-a-0'), true)
  assert.equal(isMessageDebugTimelineMarkSelected('turn:run-a-0', 'run-a-0', 'model-a'), true)
  assert.equal(isMessageDebugTimelineMarkSelected('turn:run-a-0', 'run-b-1', 'model-b'), false)

  assert.equal(isMessageDebugTimelineMarkSelected('item:run-a-0:model-a', 'run-a-0'), false)
  assert.equal(
    isMessageDebugTimelineMarkSelected('item:run-a-0:model-a', 'run-a-0', 'model-a'),
    true
  )
  assert.equal(
    isMessageDebugTimelineMarkSelected('item:run-a-0:model-a', 'run-a-0', 'tool-a'),
    false
  )
  assert.equal(isMessageDebugTimelineMarkSelected('', 'run-a-0', 'model-a'), false)
})

test('调试概览解开 MCP 文本块，格式化 JSON 并保留普通文本和路径', () => {
  assert.equal(formatMessageDebugContent('第一行\n第二行'), '第一行\n第二行')
  assert.equal(
    formatMessageDebugContent([{ type: 'text', text: '内容' }]),
    '内容'
  )
  assert.equal(
    formatMessageDebugContent(JSON.stringify([{ type: 'text', text: '{"apps": ["编辑器"]}' }])),
    '{\n  "apps": [\n    "编辑器"\n  ]\n}'
  )
  assert.equal(formatMessageDebugContent(JSON.stringify('第一行\n第二行')), '第一行\n第二行')
  assert.equal(formatMessageDebugContent('C:\\new\\test.txt'), 'C:\\new\\test.txt')
  assert.equal(formatMessageDebugContent('{无效 JSON}'), '{无效 JSON}')
  assert.equal(formatMessageDebugContent('null'), 'null')
  assert.equal(formatMessageDebugContent('[1,2]'), '[\n  1,\n  2\n]')
  assert.equal(
    formatMessageDebugContent([{ type: 'image', data: '保留非文本块' }]),
    '[\n  {\n    "type": "image",\n    "data": "保留非文本块"\n  }\n]'
  )
  assert.equal(formatMessageDebugContent(null), '')
})

test('Turn 完整导出保留所有 Run、原始字段和未筛选记录', async () => {
  const records = [
    { type: 'human', content: '用户问题', input_id: 'input-a', run_id: 'run-a' },
    { type: 'ai', content: '模型正文', reasoning_content: '完整保留推理', run_id: 'run-b' }
  ]
  const group = {
    turnId: 'turn-a', inputId: 'input-a', turnTrace: { status: 'completed', created_at: 'time' },
    runs: [{ runTrace: { run_id: 'run-a' } }, { runTrace: { run_id: 'run-b' } }],
    items: buildMessageDebugEntries(records), visibleItems: []
  }
  const before = structuredClone(group)
  const expected = {
    status: 'completed', created_at: 'time', turn_id: 'turn-a', input_id: 'input-a',
    runs: [{ run_id: 'run-a' }, { run_id: 'run-b' }], records
  }
  assert.deepEqual(buildMessageDebugTurnData(group), expected)
  assert.deepEqual(JSON.parse(await serializeMessageDebugTurn(group)), expected)
  assert.deepEqual(group, before)
})

test('精简导出去除推理、内部字段和重复调用，保留未执行工具与失败信息', async () => {
  const records = [
    { type: 'human', content: '用户问题', run_id: 'run-a' },
    { type: 'ai', run_id: 'run-a', content: [
      { type: 'reasoning', reasoning: '不能泄漏的推理' },
      { type: 'text', text: '先查看文件' }, { type: 'tool_use', id: 'call-a', input: '重复参数' }
    ], tool_calls: [{ id: 'call-a', name: 'read_file', args: { path: '/a' } }],
    content_blocks: ['重复正文'], reasoning_content: '内部推理' },
    { type: 'tool', run_id: 'run-a', operation_id: 'call-a', tool_name: 'read_file',
      tool_input: { path: '/a' }, content: [{ type: 'text', text: '文件正文' }],
      tool_output: { duplicate: '文件正文' }, error_message: '读取警告' },
    { type: 'ai', run_id: 'run-b', content: '', tool_calls: [
      { id: 'call-a', function: { name: 'read_file', arguments: '{"path":"/b"}' } }
    ] },
    { type: 'ai', run_id: 'run-b', content: '完成', error_message: '模型错误', error_type: 'provider' },
    { type: 'system', content: '内部说明' }
  ]
  const group = { items: buildMessageDebugEntries(records) }
  const before = structuredClone(group)
  assert.deepEqual(JSON.parse(await serializeMessageDebugTurn(group, 'compact')), { messages: [
    { role: 'user', content: '用户问题' },
    { role: 'assistant', content: '先查看文件' },
    { role: 'tool', name: 'read_file', arguments: { path: '/a' }, result: '文件正文', error: '读取警告' },
    { role: 'assistant', content: '', tool_calls: [{ name: 'read_file', arguments: '{"path":"/b"}' }] },
    { role: 'assistant', content: '完成', error: '模型错误' }
  ] })
  assert.deepEqual(JSON.parse(await serializeMessageDebugTurn(group, 'minimal')), { messages: [
    { role: 'user', content: '用户问题' },
    { role: 'assistant', content: '先查看文件' },
    { role: 'assistant', content: '完成' }
  ] })
  assert.deepEqual(group, before)
})

test('详情面板拖动保留记录区并约束异常高度', () => {
  assert.equal(constrainMessageDebugInspectorHeight(600, 260), 260)
  assert.equal(constrainMessageDebugInspectorHeight(600, -50), 180)
  assert.equal(constrainMessageDebugInspectorHeight(600, 900), 476)
  assert.equal(constrainMessageDebugInspectorHeight(200, 180), 76)
  assert.equal(constrainMessageDebugInspectorHeight(0, 100), null)
})

test('宽屏详情面板拖动保留记录区并约束异常宽度', () => {
  assert.equal(constrainMessageDebugInspectorWidth(1200, 504), 504)
  assert.equal(constrainMessageDebugInspectorWidth(1200, -50), 260)
  assert.equal(constrainMessageDebugInspectorWidth(1200, 900), 896)
  assert.equal(constrainMessageDebugInspectorWidth(700, 500), 396)
  assert.equal(constrainMessageDebugInspectorWidth(600, 252), 260)
  assert.equal(constrainMessageDebugInspectorWidth(0, 100), null)
})

test('消息身份按 metadata 优先，并显式控制 human id fallback', () => {
  const message = {
    id: 'human-1',
    type: 'human',
    input_id: 'input-direct',
    run_id: 'run-direct',
    extra_metadata: {
      input_id: 'input-meta',
      run_id: 'run-meta'
    }
  }

  assert.equal(getMessageInputId(message), 'input-meta')
  assert.equal(getMessageRunId(message), 'run-meta')
  assert.equal(getMessageInputId({ id: 'human-2', type: 'human' }), null)
  assert.equal(
    getMessageInputId({ id: 'human-2', type: 'human' }, { allowMessageIdFallback: true }),
    'human-2'
  )
})

test('消息调试条目保持后端数组顺序并保留独立工具消息', () => {
  const history = [
    { id: 1, type: 'human', content: '请查询' },
    {
      id: 2,
      type: 'ai',
      content: '开始查询',
      tool_calls: [{ name: 'search_kb' }, { function: { name: 'read_file' } }]
    },
    { id: 3, type: 'tool', name: 'search_kb', content: '查询结果' },
    { id: 4, type: 'system', content: '系统提示' }
  ]

  const entries = buildMessageDebugEntries(history)

  assert.deepEqual(
    entries.map((entry) => entry.id),
    ['1', '2', '3', '4']
  )
  assert.deepEqual(
    entries.map((entry) => entry.role),
    ['human', 'ai', 'tool', 'system']
  )
  assert.equal(entries[1].summary, '开始查询')
  assert.deepEqual(entries[1].raw.tool_calls, history[1].tool_calls)
  assert.equal(entries[2].summary, '工具: search_kb | 查询结果')
})

test('无正文的模型消息用 Tool Calls 摘要代替 [AI 回复] 占位', () => {
  const entries = buildMessageDebugEntries([
    {
      id: 'ai-tools',
      type: 'ai',
      content: '',
      tool_calls: [{ name: 'execute' }, { function: { name: 'write_file' } }]
    },
    { id: 'ai-empty', type: 'ai', content: '' },
    { id: 'ai-text', type: 'ai', content: '直接回答' }
  ])

  assert.equal(entries[0].summary, 'Tool Calls: execute, write_file')
  assert.equal(entries[0].roleLabel, 'AI · Tools')
  assert.equal(entries[1].summary, '[AI 回复]')
  assert.equal(entries[2].summary, '直接回答')
})

test('消息调试按连续 Run 分组且不猜测无 run_id 消息的归属', () => {
  const entries = buildMessageDebugEntries([
    { id: 'user-a', type: 'human', run_id: 'run-a', content: '问题 A' },
    { id: 'ai-a', type: 'ai', extra_metadata: { run_id: 'run-a' }, content: '回答 A' },
    { id: 'system', type: 'system', content: '未关联消息' },
    { id: 'user-b', type: 'human', run_id: 'run-b', content: '问题 B' }
  ])

  const groups = groupMessageDebugEntries(entries)

  assert.deepEqual(
    groups.map((group) => group.runId),
    ['run-a', null, 'run-b']
  )
  assert.deepEqual(
    groups.map((group) => group.items.map((entry) => entry.id)),
    [['user-a', 'ai-a'], ['system'], ['user-b']]
  )
})

test('AgentRun 投影为零消息取消 Run 补齐可检查分组', () => {
  const entries = buildMessageDebugEntries([
    { id: 'user-a', type: 'human', run_id: 'run-a', content: '问题 A' }
  ])
  const groups = mergeMessageDebugRunGroups(entries, [
    { run_id: 'run-a', status: 'completed' },
    { run_id: 'run-cancelled', status: 'cancelled' },
    { run_id: 'run-cancelled', status: 'cancelled' }
  ])

  assert.deepEqual(
    groups.map((group) => ({ runId: group.runId, items: group.items.length })),
    [
      { runId: 'run-a', items: 1 },
      { runId: 'run-cancelled', items: 0 }
    ]
  )
})

test('AgentRun 投影补组时不重排未关联或重复 Run 的事实顺序', () => {
  const entries = buildMessageDebugEntries([
    { id: 'run-a-first', type: 'human', run_id: 'run-a', content: '问题 A' },
    { id: 'unassigned', type: 'system', content: '未关联事实' },
    { id: 'run-a-second', type: 'ai', run_id: 'run-a', content: '回答 A' },
    { id: 'run-b', type: 'human', run_id: 'run-b', content: '问题 B' }
  ])
  const groups = mergeMessageDebugRunGroups(entries, [
    { run_id: 'run-a', status: 'completed' },
    { run_id: 'run-missing', status: 'cancelled' },
    { run_id: 'run-b', status: 'completed' }
  ])

  assert.deepEqual(
    groups.map((group) => ({ runId: group.runId, ids: group.items.map((item) => item.id) })),
    [
      { runId: 'run-a', ids: ['run-a-first'] },
      { runId: null, ids: ['unassigned'] },
      { runId: 'run-a', ids: ['run-a-second'] },
      { runId: 'run-missing', ids: [] },
      { runId: 'run-b', ids: ['run-b'] }
    ]
  )
})

test('模型调试条目只保留模型自身时间，Run 由独立列表分组', () => {
  const [entry] = buildMessageDebugEntries([
    {
      id: 'ai-a', type: 'ai', run_id: 'run-a',
      started_at: '2026-09-05T00:00:01Z', duration_ms: 800
    }
  ])
  assert.equal(entry.durationMs, 800)
  assert.equal('runTiming' in entry, false)
  assert.equal(entry.runId, 'run-a')
})

test('Langfuse Trace 地址仅接受后端返回的 HTTP(S) URL', () => {
  assert.equal(
    resolveLangfuseTraceUrl({
      langfuse_url: 'https://langfuse.example/project/project-1/traces/trace-1'
    }),
    'https://langfuse.example/project/project-1/traces/trace-1'
  )
  assert.equal(resolveLangfuseTraceUrl({ langfuse_url: 'javascript:alert(1)' }), null)
  assert.equal(
    resolveLangfuseTraceUrl({ langfuse_url: null }),
    null
  )
})

test('Turn 详情按稳定 turn_id 打开 Langfuse Trace', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/MessageDebugPanel.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /打开 Langfuse Trace/)
  assert.match(source, /openTurnInLangfuse\(selectedTarget\.group\.turnId\)/)
  assert.match(source, /agentApi\.getTurnLangfuse\(threadId, turnId\)/)
  assert.match(source, /resolveLangfuseTraceUrl\(result\)/)
})

test('Turn 行只读取审计接口返回的 Turn 状态而不从 Run 终态猜测', () => {
  const source = readFileSync(
    new URL('../../src/modules/session/ui/MessageDebugPanel.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /runTraces\.value = Array\.isArray\(result\?\.runs\)/)
  const persistedStatusRead = source.indexOf(
    'if (group.turnTrace?.status) return group.turnTrace.status'
  )
  assert.ok(persistedStatusRead >= 0)
  assert.doesNotMatch(source, /if \(props\.runActive && props\.activeRunId/)
  assert.doesNotMatch(source, /terminalModel/)
})

test('失败 Tool 审计展示错误而不把 running wall-clock 推算成耗时', () => {
  const [entry] = buildMessageDebugEntries([
    {
      id: 11,
      type: 'tool',
      run_id: 'run-1',
      operation_id: 'call-error',
      tool_name: 'search',
      tool_input: { q: 'Yuxi' },
      error_message: 'provider unavailable',
      execution_status: 'failed'
    }
  ])

  assert.equal(entry.summary, '错误: provider unavailable')
  assert.equal(entry.durationMs, null)
})

test('Model/Tool monotonic 耗时在分钟边界正确进位', () => {
  assert.equal(formatAuditDuration(675), '675 ms')
  assert.equal(formatAuditDuration(1120), '1.12 s')
  assert.equal(formatAuditDuration(60_000), '1m 0s')
  assert.equal(formatAuditDuration(119_600), '2m 0s')
  assert.equal(formatAuditDuration(null), '')
})

test('工具名称按多种消息字段解析并去重', () => {
  const names = extractMessageToolNames({
    tool_calls: [
      { name: 'search' },
      { tool_name: 'search' },
      { function: { name: 'read_file' } },
      {}
    ]
  })

  assert.deepEqual(names, ['search', 'read_file'])
})

test('Trace 记录位置只使用持久绝对时间，不从 monotonic 耗时推算', () => {
  const entries = buildMessageDebugEntries([
    {
      id: 'user-1',
      type: 'human',
      created_at: '2026-09-04T08:00:01Z',
      run_id: 'run-1'
    },
    {
      id: 'model-1',
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'model-1',
      started_at: '2026-09-04T08:00:02Z',
      finished_at: '2026-09-04T08:00:06Z',
      duration_ms: 3998
    },
    {
      id: 'tool-without-time',
      type: 'tool',
      run_id: 'run-1',
      duration_ms: 800
    }
  ])
  const runWindow = {
    startMs: Date.parse('2026-09-04T08:00:00Z'),
    endMs: Date.parse('2026-09-04T08:00:08Z'),
    durationMs: 8000
  }

  assert.deepEqual(getMessageDebugEntryTimeRange(entries[0]), {
    startMs: Date.parse('2026-09-04T08:00:01Z'),
    endMs: Date.parse('2026-09-04T08:00:01Z')
  })
  assert.equal(getMessageDebugEntryTimeRange(entries[2]), null)
  assert.deepEqual(
    buildMessageDebugTraceSpans(entries, runWindow).map(({ key, startOffsetMs, endOffsetMs }) => ({
      key,
      startOffsetMs,
      endOffsetMs
    })),
    [
      { key: 'user-1', startOffsetMs: 1000, endOffsetMs: 1000 },
      { key: 'run-1:assistant:model-1', startOffsetMs: 2000, endOffsetMs: 6000 },
      { key: 'tool-without-time', startOffsetMs: 0, endOffsetMs: 8000 }
    ]
  )
  const fallbackSpan = buildMessageDebugTraceSpans(entries, runWindow).at(-1)
  assert.equal(fallbackSpan.timingFallback, true)
  assert.equal(
    isMessageDebugTimelineMarkSelected(
      `item:run-1-0:${fallbackSpan.key}`,
      'run-1-0',
      fallbackSpan.key
    ),
    true
  )
})

test('调试投影把后端无时区数据库时间明确解释为 UTC', () => {
  const [entry] = buildMessageDebugEntries([
    {
      id: 'user-1',
      type: 'human',
      created_at: '2026-09-04T11:09:41.123456',
      run_id: 'run-1'
    }
  ])

  assert.equal(entry.createdAt, '2026-09-04T11:09:41.123456Z')
  assert.equal(
    getMessageDebugEntryTimeRange(entry).startMs,
    Date.parse('2026-09-04T11:09:41.123456Z')
  )
})

test('范围筛选把 Run 内记录映射到拼接后的累计执行时间', () => {
  const runWindow = {
    startMs: Date.parse('2026-09-04T08:00:00Z'),
    durationMs: 10_000
  }
  const outside = { createdAt: '2026-09-04T08:00:01Z' }
  const inside = { startedAt: '2026-09-04T08:00:05Z', finishedAt: '2026-09-04T08:00:06Z' }
  const context = { durationMs: 750 }

  assert.equal(isMessageDebugEntryInTimeRange(outside, runWindow, 10_000, 30_000, 0.45, 0.6), false)
  assert.equal(isMessageDebugEntryInTimeRange(inside, runWindow, 10_000, 30_000, 0.45, 0.6), true)
  assert.equal(isMessageDebugEntryInTimeRange(context, runWindow, 10_000, 30_000, 0.45, 0.6), true)
})

test('会话范围筛选按 Run 拼接位置处理无时间记录', () => {
  const earlyRunWindow = {
    startMs: Date.parse('2026-09-04T08:00:00Z'),
    endMs: Date.parse('2026-09-04T08:00:04Z'),
    durationMs: 4000
  }
  const lateRunWindow = {
    startMs: Date.parse('2026-09-04T08:00:12Z'),
    endMs: Date.parse('2026-09-04T08:00:16Z'),
    durationMs: 4000
  }

  assert.equal(isMessageDebugEntryInTimeRange({}, earlyRunWindow, 0, 8000, 0.625, 1), false)
  assert.equal(isMessageDebugEntryInTimeRange({}, lateRunWindow, 4000, 8000, 0.625, 1), true)
})

test('待运行请求独立分组，Run 到达后保持分组和用户记录标识', () => {
  const messages = [
    {
      id: 'req-a',
      type: 'human',
      input_id: 'req-a',
      created_at: '2026-09-05T06:32:00Z',
      content: 'A'
    },
    { id: 'req-b', type: 'human', input_id: 'req-b', delivery_status: 'queued', content: 'B' }
  ]
  const before = groupMessageDebugEntries(buildMessageDebugEntries(messages))
  assert.equal(before.length, 2)
  assert.equal(before[0].inputId, 'req-a')
  assert.equal(before[1].inputId, 'req-b')
  const after = groupMessageDebugEntries(
    buildMessageDebugEntries([
      { ...messages[0], id: 101, run_id: 'run-a' },
      { id: 102, type: 'ai', run_id: 'run-a', content: '回答 A' },
      messages[1]
    ])
  )
  assert.equal(after[0].key, before[0].key)
  assert.equal(after[0].items[0].id, before[0].items[0].id)
  assert.equal(after[0].items.length, 2)
  assert.equal(after[1].runId, null)
  assert.equal(after[1].key, before[1].key)
})

test('明确接入关联只更新对应用户消息，保留已有运行事实', () => {
  const messages = [
    { type: 'human', input_id: 'req-a', created_at: '2026-09-05T06:32:00Z' },
    { type: 'human', input_id: 'req-b' },
    { type: 'ai', input_id: 'req-a' },
    { type: 'human', input_id: 'req-a', run_id: 'existing-run' }
  ]
  bindMessageInputRun(messages, 'req-a', 'run-a')
  assert.equal(getMessageRunId(messages[0]), 'run-a')
  assert.equal(messages[0].created_at, '2026-09-05T06:32:00Z')
  assert.equal(getMessageRunId(messages[1]), null)
  assert.equal(getMessageRunId(messages[2]), null)
  assert.equal(getMessageRunId(messages[3]), 'existing-run')
  bindMessageInputRun(messages, 'req-b', null)
  assert.equal(getMessageRunId(messages[1]), null)
})

test('非连续同请求分段具有独立 key，不抢占其他分段的选择', () => {
  const groups = groupMessageDebugEntries(
    buildMessageDebugEntries([
      { id: 1, type: 'human', input_id: 'req-a', run_id: 'run-a' },
      { id: 2, type: 'human', input_id: 'req-b' },
      { id: 3, type: 'ai', input_id: 'req-a', run_id: 'run-a' }
    ])
  )
  assert.equal(groups.length, 3)
  assert.equal(new Set(groups.map((group) => group.key)).size, 3)
})
