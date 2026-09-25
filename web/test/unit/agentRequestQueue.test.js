import assert from 'node:assert/strict'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { createServer } from 'vite'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
let server
let agentApi
let useAgentRequestQueue
let useAgentRunStream
let dispatchRunEventChunks
let useAgentStreamHandler
let MessageProcessor
let getConversationDisplayItems
let groupConversationContinuations

before(async () => {
  const storage = new Map()
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key)
  }
  server = await createServer({ root: webRoot, server: { middlewareMode: true } })
  ;({ agentApi } = await server.ssrLoadModule('/src/apis/index.js'))
  ;({ useAgentRequestQueue } = await server.ssrLoadModule(
    '/src/composables/useAgentRequestQueue.js'
  ))
  ;({ useAgentRunStream, dispatchRunEventChunks } = await server.ssrLoadModule(
    '/src/composables/useAgentRunStream.js'
  ))
  ;({ useAgentStreamHandler } = await server.ssrLoadModule(
    '/src/composables/useAgentStreamHandler.js'
  ))
  ;({ default: MessageProcessor } = await server.ssrLoadModule('/src/utils/messageProcessor.js'))
  ;({ getConversationDisplayItems } = await server.ssrLoadModule('/src/utils/messageGrouping.js'))
  ;({ groupConversationContinuations } = await server.ssrLoadModule(
    '/src/utils/conversationProcessGrouping.js'
  ))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
})

test('审批前后的工具和思考跨关联 Run 连续展示，正文仍独立', () => {
  const runs = [
    { run_id: 'first', status: 'interrupted', timing: { created_at: '2026-09-16T00:00:00Z' } },
    {
      run_id: 'resume',
      run_type: 'resume',
      created_by_run_id: 'first',
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
      content: '同意',
      extra_metadata: { source: 'ask_user_question_resume' }
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
  const groups = groupConversationContinuations(runGroups)
  assert.equal(groups.length, 1)
  const items = getConversationDisplayItems(groups[0])
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
  const streaming = groupConversationContinuations([
    runGroups[0],
    { ...runGroups[1], status: 'streaming', messages: runGroups[1].messages.slice(0, 1) }
  ])
  assert.equal(streaming[0].status, 'streaming')
  assert.equal(getConversationDisplayItems(streaming[0])[1].key, items[1].key)
})

test('thinking 与相邻工具按顺序合并，正文和错误仍独立显示', () => {
  const messages = [
    { id: 'a1', type: 'ai', reasoning_content: '先检查', tool_calls: [{ id: 't1', name: 'ls', args: {} }] },
    { id: 'a2', type: 'ai', reasoning_content: '再确认', tool_calls: [{ id: 't2', name: 'read_file', args: {} }] },
    { id: 'a3', type: 'ai', reasoning_content: '得到结论', content: '最终回答' }
  ]
  const items = getConversationDisplayItems({ messages })
  assert.deepEqual(items.map((item) => item.type), ['tool-group', 'message'])
  assert.deepEqual(items[0].entries.map((entry) => entry.type), ['reasoning', 'tool', 'reasoning', 'tool', 'reasoning'])
  assert.deepEqual(items[0].entries.filter((entry) => entry.type === 'reasoning').map((entry) => entry.content), ['先检查', '再确认', '得到结论'])
  assert.equal(items[0].toolCalls.length, 2)
  assert.equal(items[1].message.content, '最终回答')
  assert.equal(items[1].message.reasoning_content, '')
  assert.equal(messages[2].reasoning_content, '得到结论')

  const thinkingOnly = getConversationDisplayItems({ messages: [{ id: 'a', type: 'ai', reasoning_content: '思考中' }] })
  assert.equal(thinkingOnly.length, 1)
  assert.equal(thinkingOnly[0].type, 'tool-group')
  assert.equal(thinkingOnly[0].entries[0].content, '思考中')
  assert.deepEqual(thinkingOnly[0].toolCalls, [])
  assert.deepEqual(getConversationDisplayItems({ messages: [{ type: 'ai', content: '' }] }), [])

  const failed = getConversationDisplayItems({ messages: [{ type: 'ai', reasoning_content: '检查中断', error_type: 'interrupted' }] })
  assert.deepEqual(failed.map((item) => item.type), ['tool-group', 'message'])
  assert.equal(failed[1].message.error_type, 'interrupted')
})

test('同一消息的 thinking、正文和工具分段使用不同的稳定 key', () => {
  const message = { id: 'mixed', type: 'ai', reasoning_content: '计划', content: '先查文件', tool_calls: [{ id: 't1', name: 'ls', args: {} }] }
  const items = getConversationDisplayItems({ messages: [message] })
  assert.deepEqual(items.map((item) => item.type), ['tool-group', 'message', 'tool-group'])
  assert.equal(new Set(items.map((item) => item.key)).size, 3)
  assert.equal(items[0].entries[0].content, '计划')
  assert.equal(items[2].entries[0].toolCall.id, 't1')
  const updated = getConversationDisplayItems({ messages: [{ ...message, content: '先查文件，再整理' }] })
  assert.deepEqual(updated.map((item) => item.key), items.map((item) => item.key))
})

test('当前工具语义流首块即显示，完整快照覆盖分片并关联同 Run 结果', () => {
  const threadState = { onGoingConv: { msgChunks: {} } }
  const { handleStreamChunk } = useAgentStreamHandler({ getThreadState: () => threadState })
  const send = (stream_event) =>
    handleStreamChunk({ status: 'loading', run_id: 'run-tools', stream_event }, 'thread-tools')
  const messages = () =>
    MessageProcessor.convertToolResultToMessages(
      Object.values(threadState.onGoingConv.msgChunks).map(MessageProcessor.mergeMessageChunk)
    ).filter((msg) => msg.type !== 'tool')
  send({
    type: 'tool_call_delta',
    message_id: 'model-tools',
    tool_call_id: 'call-1',
    name: 'read_file',
    index: 0,
    args_delta: '{"path":'
  })
  let items = getConversationDisplayItems({ messages: messages() })
  assert.equal(items.length, 1)
  assert.equal(items[0].type, 'tool-group')
  assert.equal(items[0].toolCalls[0].name, 'read_file')
  send({ type: 'tool_call_delta', message_id: 'model-tools', index: 0, args_delta: '"/notes.md"}' })
  send({
    type: 'tool_call',
    message_id: 'model-tools',
    tool_call_id: 'call-1',
    name: 'read_file',
    index: 0,
    args: { path: '/notes.md' }
  })
  send({
    type: 'tool_call',
    message_id: 'model-tools',
    tool_call_id: 'call-2',
    name: 'ls',
    index: 1,
    args: { path: '/' }
  })
  handleStreamChunk(
    {
      status: 'stream_event',
      run_id: 'run-tools',
      event: {
        method: 'tools',
        data: {
          event: 'tool-finished',
          tool_call_id: 'call-1',
          output: { type: 'tool', tool_call_id: 'call-1', content: 'notes', status: 'success' }
        }
      }
    },
    'thread-tools'
  )
  const toolCalls = messages()[0].tool_calls
  assert.equal(toolCalls.length, 2)
  assert.deepEqual(JSON.parse(toolCalls[0].args), { path: '/notes.md' })
  assert.equal(toolCalls[0].tool_call_result.content, 'notes')
  assert.equal(toolCalls[0].tool_call_result.run_id, 'run-tools')
  assert.equal(toolCalls[1].tool_call_result, null)
})

test('只有单个完整工具事件且无正文时仍显示工具，旧 loading msg 不再消费', () => {
  const threadState = { onGoingConv: { msgChunks: {} } }
  const { handleStreamChunk } = useAgentStreamHandler({ getThreadState: () => threadState })
  handleStreamChunk(
    { status: 'loading', msg: { id: 'legacy', type: 'ai', content: 'old' } },
    'thread-tools'
  )
  assert.deepEqual(threadState.onGoingConv.msgChunks, {})
  handleStreamChunk(
    {
      status: 'loading',
      run_id: 'run-tools',
      stream_event: {
        type: 'tool_call',
        message_id: 'only-tool',
        tool_call_id: 'call-1',
        name: 'ls',
        args: {}
      }
    },
    'thread-tools'
  )
  const message = MessageProcessor.mergeMessageChunk(threadState.onGoingConv.msgChunks['only-tool'])
  assert.equal(getConversationDisplayItems({ messages: [message] })[0].toolCalls[0].name, 'ls')
})

test('重复工具 ID 的结果不能跨 Run 绑定', () => {
  const [message] = MessageProcessor.convertToolResultToMessages([
    { type: 'ai', run_id: 'new-run', tool_calls: [{ id: 'same-call', name: 'ls', args: {} }] },
    { type: 'tool', run_id: 'old-run', tool_call_id: 'same-call', content: 'old result' }
  ])
  assert.equal(message.tool_calls[0].tool_call_result, null)
})

/** 集中 Run SSE 测试的固定依赖，只暴露各用例关心的行为。 */
const createRunStream = ({ threadState, handleStreamChunk, resetOnGoingConv }) =>
  useAgentRunStream({
    getThreadState: () => threadState,
    currentAgentId: { value: 'agent-1' },
    handleStreamChunk,
    fetchThreadMessages: async () => {},
    fetchAgentState: () => {},
    resetOnGoingConv,
    onScrollToBottom: () => {},
    streamSmoother: { flushThread: () => {} }
  })

test('Run envelope 归一化同时保留批量与单 chunk 形状及线程上下文', () => {
  const batchData = {
    request_id: 'request-1',
    run_id: 'run-1',
    payload: { items: [{ status: 'loading', id: 'message-1' }] }
  }
  const singleData = {
    request_id: 'request-2',
    payload: {
      chunk: {
        status: 'loading',
        id: 'message-2',
        metadata: { thread_id: 'child-thread' }
      }
    }
  }

  const dispatched = []
  dispatchRunEventChunks({
    data: batchData,
    runId: 'run-fallback',
    fallbackThreadId: 'parent-thread',
    streamRunId: 'run-1',
    streamThreadId: 'parent-thread',
    onChunk: (chunk, threadId) => dispatched.push({ chunk, threadId })
  })
  dispatchRunEventChunks({
    data: singleData,
    runId: 'run-fallback',
    fallbackThreadId: 'parent-thread',
    onChunk: (chunk, threadId) => dispatched.push({ chunk, threadId })
  })

  assert.deepEqual(dispatched, [
    {
      chunk: {
        status: 'loading',
        id: 'message-1',
        request_id: 'request-1',
        run_id: 'run-1',
        thread_id: 'parent-thread',
        stream_run_id: 'run-1',
        stream_thread_id: 'parent-thread'
      },
      threadId: 'parent-thread'
    },
    {
      chunk: {
        status: 'loading',
        id: 'message-2',
        metadata: { thread_id: 'child-thread' },
        request_id: 'request-2',
        run_id: 'run-fallback',
        thread_id: 'child-thread'
      },
      threadId: 'child-thread'
    }
  ])
})

test('agent_state SSE 使在途状态请求失效', () => {
  const threadState = {
    agentState: null,
    agentStateRequestVersion: 4,
    onGoingConv: { msgChunks: {} }
  }
  const { handleStreamChunk } = useAgentStreamHandler({
    getThreadState: () => threadState,
    processApprovalInStream: () => false,
    currentAgentId: { value: 'agent-1' },
    supportsFiles: { value: false }
  })
  const agentState = { token_usage: { measured_at: '2026-08-09T00:00:00Z' } }

  handleStreamChunk({ status: 'agent_state', agent_state: agentState }, 'thread-1')

  assert.deepEqual(threadState.agentState, agentState)
  assert.equal(threadState.agentStateRequestVersion, 5)
})

test('Run init 将权威 run_id 绑定到实时 User Message', () => {
  const threadState = {
    pendingRequestId: 'request-1',
    replyLoadingVisible: false,
    contextCompressing: false,
    onGoingConv: {
      msgChunks: {
        'request-1': [
          { id: 'request-1', type: 'human', content: '快排', created_at: '2026-09-05T06:32:00Z' }
        ]
      }
    }
  }
  const { handleStreamChunk } = useAgentStreamHandler({
    getThreadState: () => threadState,
    processApprovalInStream: () => false,
    currentAgentId: { value: 'agent-1' },
    supportsFiles: { value: false }
  })

  handleStreamChunk(
    {
      status: 'init',
      run_id: 'stale-body-run',
      stream_run_id: 'run-1',
      stream_thread_id: 'thread-1',
      request_id: 'request-1',
      msg: { type: 'human', content: '快排' }
    },
    'thread-1'
  )

  const [message] = threadState.onGoingConv.msgChunks['request-1']
  assert.equal(message.created_at, '2026-09-05T06:32:00Z')
  assert.equal(message.run_id, 'run-1')
  assert.equal(message.extra_metadata.run_id, 'run-1')
  assert.equal(message.extra_metadata.request_id, 'request-1')
})

test('缺少 SSE request_id 时不把 Run 关联到本地 pending User', () => {
  const threadState = {
    pendingRequestId: 'request-old',
    replyLoadingVisible: false,
    contextCompressing: false,
    onGoingConv: { msgChunks: {} }
  }
  const { handleStreamChunk } = useAgentStreamHandler({
    getThreadState: () => threadState,
    processApprovalInStream: () => false,
    currentAgentId: { value: 'agent-1' },
    supportsFiles: { value: false }
  })

  handleStreamChunk(
    {
      status: 'init',
      run_id: 'run-new',
      msg: {
        type: 'human',
        content: '旧请求',
        run_id: 'run-from-msg',
        extra_metadata: { run_id: 'run-from-msg' }
      }
    },
    'thread-1'
  )

  const [message] = threadState.onGoingConv.msgChunks['request-old']
  assert.equal(message.run_id, undefined)
  assert.equal(message.extra_metadata.run_id, undefined)
})

test('子线程 init 不继承父订阅 Run 关联', () => {
  const threadState = {
    pendingRequestId: 'request-child',
    replyLoadingVisible: false,
    contextCompressing: false,
    onGoingConv: { msgChunks: {} }
  }
  const { handleStreamChunk } = useAgentStreamHandler({
    getThreadState: () => threadState,
    processApprovalInStream: () => false,
    currentAgentId: { value: 'agent-1' },
    supportsFiles: { value: false }
  })

  handleStreamChunk(
    {
      status: 'init',
      request_id: 'request-child',
      stream_run_id: 'parent-run',
      stream_thread_id: 'parent-thread',
      msg: { type: 'human', content: '子线程输入' }
    },
    'child-thread'
  )

  const [message] = threadState.onGoingConv.msgChunks['request-child']
  assert.equal(message.run_id, undefined)
})

test('恢复队列在同步后重新读取线程状态并启动当前请求流', async () => {
  const staleThreadState = {
    queuedRequests: [{ request_id: 'request-stale', status: 'queued' }],
    requestStreams: {}
  }
  const latestThreadState = {
    queuedRequests: [{ request_id: 'request-latest', status: 'running' }],
    requestStreams: {}
  }
  let currentThreadState = staleThreadState
  const calls = []
  const originalListThreadQueuedRequests = agentApi.listThreadQueuedRequests
  const originalStreamRequestEvents = agentApi.streamRequestEvents
  agentApi.listThreadQueuedRequests = async (...args) => {
    calls.push(['sync', ...args])
    currentThreadState = latestThreadState
    return {
      requests: [{ request_id: 'request-from-sync', status: 'queued' }],
      queue: { status: 'running' }
    }
  }
  agentApi.streamRequestEvents = async (requestId) =>
    new Response(`event: run_created\ndata: {"run_id":"run-for-${requestId}"}\n\n`, {
      headers: { 'Content-Type': 'text/event-stream' }
    })

  try {
    const streamStarts = []
    const queue = useAgentRequestQueue({
      getThreadState: () => currentThreadState,
      resetOnGoingConv: () => {},
      startRunStream: (...args) => streamStarts.push(args),
      onStreamError: () => {}
    })

    await queue.resumeQueuedRequests('thread-1', 'agent-1')
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.deepEqual(calls, [['sync', 'thread-1', 'agent-1']])
    assert.deepEqual(streamStarts, [
      ['thread-1', 'run-for-request-latest', '0-0', { requestId: 'request-latest' }]
    ])
    assert.deepEqual(staleThreadState.queuedRequests, [
      { request_id: 'request-from-sync', status: 'queued' }
    ])
    assert.equal(latestThreadState.requestStreams['request-latest'], undefined)
  } finally {
    agentApi.listThreadQueuedRequests = originalListThreadQueuedRequests
    agentApi.streamRequestEvents = originalStreamRequestEvents
  }
})

test('run_created 立即完成状态交接并订阅新 Run SSE', async () => {
  const threadState = {
    queuedRequests: [{ request_id: 'request-1', status: 'queued' }],
    requestStreams: {},
    onGoingConv: {
      msgChunks: {
        old: [],
        'request-1': [{ type: 'human', content: '待执行', request_id: 'request-1' }]
      }
    },
    pendingRequestId: null
  }
  const calls = []
  const originalStreamRequestEvents = agentApi.streamRequestEvents
  agentApi.streamRequestEvents = async () =>
    new Response('event: run_created\ndata: {"run_id":"run-2"}\n\n', {
      headers: { 'Content-Type': 'text/event-stream' }
    })

  try {
    const queue = useAgentRequestQueue({
      getThreadState: () => threadState,
      resetOnGoingConv: (threadId, options) => {
        calls.push(['reset', threadId, options])
        threadState.onGoingConv = { msgChunks: {} }
      },
      startRunStream: (...args) => {
        calls.push(['start', ...args])
      },
      onStreamError: () => {}
    })

    await queue.startRequestStream('thread-1', 'request-1')

    assert.deepEqual(calls, [
      ['reset', 'thread-1', { preserveRequestStreams: true }],
      ['start', 'thread-1', 'run-2', '0-0', { requestId: 'request-1' }]
    ])
    assert.equal(threadState.onGoingConv.msgChunks['request-1'][0].content, '待执行')
    assert.equal(threadState.onGoingConv.msgChunks.old, undefined)
    assert.equal(threadState.pendingRequestId, 'request-1')
    assert.deepEqual(threadState.queuedRequests, [])
  } finally {
    agentApi.streamRequestEvents = originalStreamRequestEvents
  }
})

test('run_created 先到达时保留旧 Run 已渲染的内容', async () => {
  const oldMessages = { 'old-message': [{ id: 'old-message', content: '旧回复' }] }
  const threadState = {
    activeRunId: 'run-1',
    queuedRequests: [{ request_id: 'request-2', status: 'queued' }],
    requestStreams: {},
    onGoingConv: { msgChunks: oldMessages },
    pendingRequestId: null
  }
  const calls = []
  const originalStreamRequestEvents = agentApi.streamRequestEvents
  agentApi.streamRequestEvents = async () =>
    new Response('event: run_created\ndata: {"run_id":"run-2"}\n\n', {
      headers: { 'Content-Type': 'text/event-stream' }
    })

  try {
    const queue = useAgentRequestQueue({
      getThreadState: () => threadState,
      resetOnGoingConv: () => calls.push(['reset']),
      startRunStream: (...args) => calls.push(['start', ...args]),
      onStreamError: () => {}
    })

    await queue.startRequestStream('thread-1', 'request-2')

    assert.deepEqual(calls, [['start', 'thread-1', 'run-2', '0-0', { requestId: 'request-2' }]])
    assert.equal(threadState.onGoingConv.msgChunks, oldMessages)
    assert.equal(threadState.pendingRequestId, 'request-2')
  } finally {
    agentApi.streamRequestEvents = originalStreamRequestEvents
  }
})

test('replacement Run SSE 的增量 chunk 会连续进入前端渲染处理', async () => {
  const oldMessages = { 'old-message': [{ id: 'old-message', content: '旧回复' }] }
  const threadState = {
    activeRunId: 'run-1',
    activeRunSteerable: true,
    queuedRequests: [{ request_id: 'request-2', status: 'queued' }],
    requestStreams: {},
    runLastSeq: '0-0',
    runStreamAbortController: null,
    replyLoadingVisible: true,
    pendingRequestId: 'request-1',
    pendingInterrupt: null,
    onGoingConv: { msgChunks: oldMessages }
  }
  const originalStreamRequestEvents = agentApi.streamRequestEvents
  const originalStreamAgentRunEvents = agentApi.streamAgentRunEvents
  const textEncoder = new TextEncoder()
  let runStreamController
  let replacementRun
  let firstLoadingChunk
  let loadingChunkCount = 0
  let resolveFirstChunk
  let resolveSecondChunk
  const firstChunkProcessed = new Promise((resolve) => {
    resolveFirstChunk = resolve
  })
  const secondChunkProcessed = new Promise((resolve) => {
    resolveSecondChunk = resolve
  })

  agentApi.streamRequestEvents = async () =>
    new Response('event: run_created\ndata: {"run_id":"run-2"}\n\n', {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  agentApi.streamAgentRunEvents = async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          runStreamController = controller
        }
      }),
      { headers: { 'Content-Type': 'text/event-stream' } }
    )

  try {
    const { handleStreamChunk } = useAgentStreamHandler({
      getThreadState: () => threadState,
      processApprovalInStream: () => false,
      currentAgentId: { value: 'agent-1' },
      supportsFiles: { value: false }
    })
    const runStream = createRunStream({
      threadState,
      handleStreamChunk: (chunk, threadId) => {
        const shouldStop = handleStreamChunk(chunk, threadId)
        if (chunk.status === 'loading') {
          loadingChunkCount += 1
          if (loadingChunkCount === 1) {
            firstLoadingChunk = chunk
            resolveFirstChunk()
          }
          if (loadingChunkCount === 2) resolveSecondChunk()
        }
        return shouldStop
      },
      resetOnGoingConv: () => {}
    })
    const queue = useAgentRequestQueue({
      getThreadState: () => threadState,
      resetOnGoingConv: () => {},
      startRunStream: (...args) => {
        replacementRun = runStream.startRunStream(...args)
        return replacementRun
      },
      onStreamError: () => {}
    })

    await queue.startRequestStream('thread-1', 'request-2')

    runStreamController.enqueue(
      textEncoder.encode(
        'id: 1-0\nevent: custom\ndata: {"run_id":"run-2","request_id":"request-2","payload":{"chunk":{"status":"loading","run_id":"run-2","request_id":"request-2","stream_event":{"type":"message_delta","message_id":"assistant-2","content":"流式"}}}}\n\n'
      )
    )
    await firstChunkProcessed
    assert.deepEqual(
      threadState.onGoingConv.msgChunks['assistant-2'].map((chunk) => chunk.content),
      ['流式']
    )
    assert.equal(firstLoadingChunk.stream_run_id, 'run-2')
    assert.equal(firstLoadingChunk.stream_thread_id, 'thread-1')

    runStreamController.enqueue(
      textEncoder.encode(
        'id: 2-0\nevent: custom\ndata: {"run_id":"run-2","request_id":"request-2","payload":{"chunk":{"status":"loading","run_id":"run-2","request_id":"request-2","stream_event":{"type":"message_delta","message_id":"assistant-2","content":"渲染"}}}}\n\n'
      )
    )
    await secondChunkProcessed
    assert.deepEqual(
      threadState.onGoingConv.msgChunks['assistant-2'].map((chunk) => chunk.content),
      ['流式', '渲染']
    )

    runStreamController.enqueue(
      textEncoder.encode(
        'id: 3-0\nevent: end\ndata: {"run_id":"run-2","payload":{"status":"completed"}}\n\n'
      )
    )
    runStreamController.close()
    await replacementRun

    assert.deepEqual(threadState.onGoingConv.msgChunks['old-message'], [
      { id: 'old-message', content: '旧回复' }
    ])
    assert.equal(threadState.runLastSeq, '3-0')
  } finally {
    agentApi.streamRequestEvents = originalStreamRequestEvents
    agentApi.streamAgentRunEvents = originalStreamAgentRunEvents
  }
})

test('旧 Run 的延迟 AbortError 不覆盖新 Run 状态', async () => {
  const threadState = {
    activeRunId: null,
    activeRunSteerable: false,
    runLastSeq: '0-0',
    runStreamAbortController: null,
    replyLoadingVisible: false,
    pendingRequestId: null,
    onGoingConv: { msgChunks: {} }
  }
  const streamControllers = new Map()
  const originalStreamAgentRunEvents = agentApi.streamAgentRunEvents
  agentApi.streamAgentRunEvents = async (runId, _afterSeq, { signal }) =>
    new Response(
      new ReadableStream({
        start(controller) {
          streamControllers.set(runId, controller)
          signal.addEventListener('abort', () => {
            const delay = runId === 'run-1' ? 10 : 0
            setTimeout(() => controller.error(new DOMException('aborted', 'AbortError')), delay)
          })
          if (runId === 'run-2') {
            controller.enqueue(
              new TextEncoder().encode(
                'event: custom\ndata: {"run_id":"run-2","request_id":"request-2","payload":{"chunk":{"status":"init","msg":{"type":"human","content":"new"}}}}\n\n'
              )
            )
          }
        }
      }),
      { headers: { 'Content-Type': 'text/event-stream' } }
    )

  const runStream = createRunStream({
    threadState,
    handleStreamChunk: (chunk) => {
      if (chunk.status !== 'init') return
      threadState.pendingRequestId = chunk.request_id
      threadState.replyLoadingVisible = true
    },
    resetOnGoingConv: () => {}
  })

  try {
    const oldRun = runStream.startRunStream('thread-1', 'run-1')
    await new Promise((resolve) => setTimeout(resolve, 0))
    const newRun = runStream.startRunStream('thread-1', 'run-2')
    await new Promise((resolve) => setTimeout(resolve, 20))

    assert.equal(threadState.activeRunId, 'run-2')
    assert.equal(threadState.pendingRequestId, 'request-2')
    assert.equal(threadState.replyLoadingVisible, true)

    threadState.runStreamAbortController.abort()
    await Promise.allSettled([oldRun, newRun])
  } finally {
    agentApi.streamAgentRunEvents = originalStreamAgentRunEvents
  }
})

test('旧 Run 终态清理保留排队 Request SSE', async () => {
  const threadState = {
    activeRunId: null,
    activeRunSteerable: false,
    runLastSeq: '0-0',
    runStreamAbortController: null,
    replyLoadingVisible: false,
    pendingRequestId: null,
    pendingInterrupt: null,
    requestStreams: { 'request-2': { controller: new AbortController() } }
  }
  const resetCalls = []
  const originalStreamAgentRunEvents = agentApi.streamAgentRunEvents
  agentApi.streamAgentRunEvents = async () =>
    new Response('event: end\ndata: {"run_id":"run-1","payload":{"status":"completed"}}\n\n', {
      headers: { 'Content-Type': 'text/event-stream' }
    })

  try {
    const runStream = createRunStream({
      threadState,
      handleStreamChunk: () => {},
      resetOnGoingConv: (_threadId, options) => resetCalls.push(options)
    })

    await runStream.startRunStream('thread-1', 'run-1')
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.deepEqual(resetCalls, [{ preserveRequestStreams: true }])
    assert.equal(threadState.requestStreams['request-2'].controller.signal.aborted, false)
  } finally {
    agentApi.streamAgentRunEvents = originalStreamAgentRunEvents
  }
})

for (const reuseCursor of [false, true]) {
  test(`数据库补发 end ${reuseCursor ? '复用游标会被丢弃（缺陷对照）' : '无游标时直接结束生成'}`, async () => {
    const threadState = {
      activeRunId: null,
      runLastSeq: '0-0',
      runStreamAbortController: null,
      replyLoadingVisible: true,
      onGoingConv: { msgChunks: {} }
    }
    const originalStream = agentApi.streamAgentRunEvents
    const originalGetRun = agentApi.getAgentRun
    let statusReads = 0
    const chunks = []
    const message =
      'event: messages\nid: 1700000000002-0\ndata: {"payload":{"chunk":{"content":"final"}}}\n\n'
    const end = `event: end\n${reuseCursor ? 'id: 1700000000002-0\n' : ''}data: {"payload":{"status":"completed"}}\n\n`
    agentApi.streamAgentRunEvents = async () => new Response(message + message + end)
    agentApi.getAgentRun = async () => {
      statusReads += 1
      return { run: { id: 'run-fallback', status: 'completed' } }
    }
    try {
      const stream = createRunStream({
        threadState,
        handleStreamChunk: (chunk) => chunks.push(chunk),
        resetOnGoingConv: () => {}
      })
      await stream.startRunStream('thread-fallback', 'run-fallback')
      assert.equal(statusReads, reuseCursor ? 1 : 0)
      assert.equal(chunks.length, 1)
      assert.equal(chunks[0].content, 'final')
      assert.equal(threadState.activeRunId, null)
      assert.equal(threadState.isStreaming, false)
      assert.equal(threadState.replyLoadingVisible, false)
      assert.equal(localStorage.getItem('active_run:thread-fallback'), null)
    } finally {
      agentApi.streamAgentRunEvents = originalStream
      agentApi.getAgentRun = originalGetRun
    }
  })
}

test('自然断流后从 PG 终态复用统一清理并刷新历史', async () => {
  const threadState = {
    activeRunId: null,
    activeRunSteerable: true,
    runLastSeq: '0-0',
    runStreamAbortController: null,
    replyLoadingVisible: true,
    pendingRequestId: 'request-1',
    pendingInterrupt: null,
    onGoingConv: { msgChunks: { 'message-1': [{ content: '实时内容' }] } }
  }
  const refreshed = []
  const notifications = []
  const resetCalls = []
  const originalStreamAgentRunEvents = agentApi.streamAgentRunEvents
  const originalGetAgentRun = agentApi.getAgentRun
  agentApi.streamAgentRunEvents = async () =>
    new Response('', { headers: { 'Content-Type': 'text/event-stream' } })
  agentApi.getAgentRun = async () => ({
    run: { id: 'run-1', status: 'completed' }
  })

  try {
    const runStream = useAgentRunStream({
      getThreadState: () => threadState,
      currentAgentId: { value: 'agent-1' },
      handleStreamChunk: () => {},
      fetchThreadMessages: async () => refreshed.push('history'),
      fetchAgentState: async () => refreshed.push('state'),
      resetOnGoingConv: (_threadId, options) => resetCalls.push(options),
      onScrollToBottom: () => {},
      streamSmoother: { flushThread: () => {} },
      onTerminalDetected: (payload) => notifications.push(payload)
    })

    await runStream.startRunStream('thread-1', 'run-1')
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(threadState.activeRunId, null)
    assert.equal(threadState.isStreaming, false)
    assert.equal(threadState.replyLoadingVisible, false)
    assert.deepEqual(resetCalls, [{ preserveRequestStreams: true }])
    assert.deepEqual(refreshed, ['history', 'state'])
    assert.deepEqual(
      notifications.map(({ runId }) => runId),
      ['run-1']
    )
  } finally {
    agentApi.streamAgentRunEvents = originalStreamAgentRunEvents
    agentApi.getAgentRun = originalGetAgentRun
  }
})

test('自然断流遇到仍持有的 pending interrupt 时保留 Run 快照', async () => {
  const threadId = 'thread-with-pending-interrupt'
  const threadState = {
    activeRunId: null,
    activeRunSteerable: true,
    runLastSeq: '5-0',
    runStreamAbortController: null,
    replyLoadingVisible: true,
    pendingRequestId: 'request-1',
    pendingInterrupt: { interruptedRunId: 'run-1', questions: [{ question: '继续吗？' }] },
    onGoingConv: { msgChunks: {} }
  }
  const interrupts = []
  const originalStreamAgentRunEvents = agentApi.streamAgentRunEvents
  const originalGetAgentRun = agentApi.getAgentRun
  agentApi.streamAgentRunEvents = async () =>
    new Response('', { headers: { 'Content-Type': 'text/event-stream' } })
  agentApi.getAgentRun = async () => ({ run: { id: 'run-1', status: 'interrupted' } })

  try {
    const runStream = useAgentRunStream({
      getThreadState: () => threadState,
      currentAgentId: { value: 'agent-1' },
      handleStreamChunk: () => {},
      fetchThreadMessages: async () => {},
      fetchAgentState: async () => {},
      resetOnGoingConv: () => {},
      onScrollToBottom: () => {},
      streamSmoother: { flushThread: () => {} },
      onInterruptDetected: ({ runId }) => interrupts.push(runId)
    })

    await runStream.startRunStream(threadId, 'run-1')

    assert.equal(threadState.activeRunId, 'run-1')
    assert.equal(threadState.isStreaming, false)
    assert.deepEqual(interrupts, ['run-1'])
    assert.equal(JSON.parse(localStorage.getItem(`active_run:${threadId}`)).run_id, 'run-1')
  } finally {
    agentApi.streamAgentRunEvents = originalStreamAgentRunEvents
    agentApi.getAgentRun = originalGetAgentRun
    localStorage.removeItem(`active_run:${threadId}`)
  }
})

test('恢复时没有 active Run 也走统一终态清理', async () => {
  const threadId = 'thread-without-active-run'
  const threadState = {
    activeRunId: 'run-old',
    activeRunSteerable: false,
    runLastSeq: '22-0',
    runStreamAbortController: null,
    isStreaming: true,
    replyLoadingVisible: true,
    pendingRequestId: 'request-old',
    pendingInterrupt: { interruptedRunId: 'run-old' },
    onGoingConv: { msgChunks: {} }
  }
  const refreshed = []
  const notifications = []
  const originalGetThreadActiveRun = agentApi.getThreadActiveRun
  agentApi.getThreadActiveRun = async () => ({ run: null })
  localStorage.removeItem(`active_run:${threadId}`)

  try {
    const runStream = useAgentRunStream({
      getThreadState: () => threadState,
      currentAgentId: { value: 'agent-1' },
      handleStreamChunk: () => {},
      fetchThreadMessages: async () => refreshed.push('history'),
      fetchAgentState: async () => refreshed.push('state'),
      resetOnGoingConv: () => {},
      onScrollToBottom: () => {},
      streamSmoother: { flushThread: () => {} },
      onTerminalDetected: (payload) => notifications.push(payload)
    })

    await runStream.resumeActiveRunForThread(threadId)
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(threadState.activeRunId, null)
    assert.equal(threadState.isStreaming, false)
    assert.equal(threadState.runLastSeq, '0-0')
    assert.equal(threadState.pendingInterrupt, null)
    assert.deepEqual(refreshed, ['history', 'state'])
    assert.deepEqual(
      notifications.map(({ runId }) => runId),
      [null]
    )
  } finally {
    agentApi.getThreadActiveRun = originalGetThreadActiveRun
    localStorage.removeItem(`active_run:${threadId}`)
  }
})

test('恢复期间出现的新 Run 不会被旧的空闲查询结果清理', async () => {
  const threadId = 'thread-with-new-run'
  const threadState = {
    activeRunId: 'run-old',
    activeRunSteerable: true,
    runLastSeq: '22-0',
    runStreamAbortController: null,
    isStreaming: true,
    replyLoadingVisible: true,
    pendingRequestId: 'request-new',
    pendingInterrupt: null,
    onGoingConv: { msgChunks: {} }
  }
  const refreshed = []
  const originalGetThreadActiveRun = agentApi.getThreadActiveRun
  agentApi.getThreadActiveRun = async () => {
    threadState.activeRunId = 'run-new'
    return { run: null }
  }
  localStorage.removeItem(`active_run:${threadId}`)

  try {
    const runStream = useAgentRunStream({
      getThreadState: () => threadState,
      currentAgentId: { value: 'agent-1' },
      handleStreamChunk: () => {},
      fetchThreadMessages: async () => refreshed.push('history'),
      fetchAgentState: async () => refreshed.push('state'),
      resetOnGoingConv: () => {},
      onScrollToBottom: () => {},
      streamSmoother: { flushThread: () => {} }
    })

    await runStream.resumeActiveRunForThread(threadId)

    assert.equal(threadState.activeRunId, 'run-new')
    assert.equal(threadState.isStreaming, true)
    assert.deepEqual(refreshed, [])
  } finally {
    agentApi.getThreadActiveRun = originalGetThreadActiveRun
    localStorage.removeItem(`active_run:${threadId}`)
  }
})

test('同步队列保留尚未收到接入响应的发送项，并以服务端已接收请求去重', async () => {
  const threadState = {
    queuedRequests: [
      { request_id: 'sending', status: 'sending', content: '正在提交' },
      { request_id: 'accepted', status: 'sending', content: '已接收' }
    ]
  }
  const original = agentApi.listThreadQueuedRequests
  agentApi.listThreadQueuedRequests = async () => ({
    requests: [{ request_id: 'accepted', status: 'queued' }]
  })
  try {
    const queue = useAgentRequestQueue({ getThreadState: () => threadState })
    await queue.syncQueuedRequests('thread-1', 'agent-1')
    assert.deepEqual(
      threadState.queuedRequests.map(({ request_id, status }) => [request_id, status]),
      [
        ['accepted', 'queued'],
        ['sending', 'sending']
      ]
    )
  } finally {
    agentApi.listThreadQueuedRequests = original
  }
})

test('接入未完成的发送项只展示，不订阅或取消尚未持久化的 Request', async () => {
  const threadState = { queuedRequests: [{ request_id: 'sending', status: 'sending' }] }
  const originals = [
    agentApi.listThreadQueuedRequests,
    agentApi.streamRequestEvents,
    agentApi.cancelRequest
  ]
  const unexpected = []
  agentApi.listThreadQueuedRequests = async () => ({ requests: [] })
  agentApi.streamRequestEvents = async () => {
    unexpected.push('stream')
    throw new Error('请求尚未入库')
  }
  agentApi.cancelRequest = async () => {
    unexpected.push('cancel')
    throw new Error('请求尚未入库')
  }
  try {
    const queue = useAgentRequestQueue({ getThreadState: () => threadState })
    await queue.resumeQueuedRequests('thread-1', 'agent-1')
    assert.equal(await queue.cancelRequest('thread-1', 'sending'), false)
    assert.deepEqual(unexpected, [])
    assert.deepEqual(threadState.queuedRequests, [{ request_id: 'sending', status: 'sending' }])
  } finally {
    ;[agentApi.listThreadQueuedRequests, agentApi.streamRequestEvents, agentApi.cancelRequest] =
      originals
  }
})
