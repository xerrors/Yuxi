/** 将消息内容压缩为单行摘要。 */
function summarizeContent(content, limit) {
  if (typeof content !== 'string') return ''
  return content.replace(/\s+/g, ' ').trim().slice(0, limit)
}

/** 解开合法 JSON 和文本块的展示包装，保留普通文本中的反斜线。 */
export function formatMessageDebugContent(content) {
  if (content === undefined || content === null) return ''
  if (typeof content === 'string') {
    if (!/^[{["]/.test(content.trimStart())) return content
    try {
      return formatMessageDebugContent(JSON.parse(content))
    } catch {
      return content
    }
  }
  if (
    Array.isArray(content) &&
    content.some((block) => ['text', 'input_text', 'output_text'].includes(block?.type))
  ) {
    return content
      .map((block) =>
        ['text', 'input_text', 'output_text'].includes(block?.type) &&
        typeof block.text === 'string'
          ? formatMessageDebugContent(block.text)
          : JSON.stringify(block, null, 2)
      )
      .join('\n\n')
  }
  try {
    return JSON.stringify(content, null, 2)
  } catch {
    return String(content)
  }
}

/** 保留完整 Turn 的原始审计与运行字段，不使用筛选后的记录。 */
export function buildMessageDebugTurnData(group) {
  return {
    ...group.turnTrace,
    turn_id: group.turnId,
    input_id: group.inputId || null,
    status:
      group.turnTrace?.status ||
      (!group.turnId && group.inputId
        ? group.items.find((item) => item.role === 'human')?.raw?.delivery_status
        : null) ||
      null,
    runs: group.runs.map((run) => run.runTrace),
    records: group.items.map((item) => item.raw)
  }
}

/** 只提取消息正文，排除 reasoning、工具声明和非文本内容块。 */
function messageBodyText(content) {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((block) => ['text', 'input_text', 'output_text'].includes(block?.type))
    .map((block) => (typeof block.text === 'string' ? block.text : ''))
    .join('')
}

/** 以同一投影生成复制和下载内容；仅精简档加载 tokenizer。 */
export async function serializeMessageDebugTurn(group, detail = 'full') {
  if (detail === 'full') return JSON.stringify(buildMessageDebugTurnData(group), null, 2)
  const truncate =
    detail === 'compact'
      ? (await import('./messageDebugTokens.js')).truncateMessageDebugToolValue
      : null
  const toolRecords = group.items.filter((item) => item.role === 'tool')
  const messages = []
  for (const item of group.items) {
    const raw = item.raw
    if (item.role === 'tool' && truncate) {
      messages.push({
        role: 'tool',
        name: item.toolName,
        arguments: truncate(raw.tool_input ?? {}),
        result: truncate(formatMessageDebugContent(raw.content ?? raw.tool_output)),
        ...(raw.error_message ? { error: truncate(raw.error_message) } : {})
      })
      continue
    }
    if (!['human', 'ai', 'error'].includes(item.role)) continue
    const content = messageBodyText(raw.content)
    const entry = { role: item.role === 'human' ? 'user' : 'assistant', content }
    if (truncate && item.role !== 'human') {
      // 已有工具审计拥有参数与结果；仅补充尚无审计记录的调用声明。
      const calls = (raw.tool_calls || []).filter(
        (call) =>
          !toolRecords.some(
            (tool) =>
              call.id &&
              tool.runId === item.runId &&
              call.id === (tool.raw.tool_call_id || tool.operationId)
          )
      )
      if (calls.length) {
        entry.tool_calls = calls.map((call) => ({
          name: call.name || call.function?.name || call.tool_name,
          arguments: truncate(call.args ?? call.function?.arguments ?? {})
        }))
      }
      if (raw.error_message) entry.error = raw.error_message
    }
    if (content || entry.tool_calls || entry.error) messages.push(entry)
  }
  return JSON.stringify({ messages }, null, 2)
}

/** 判断时间概览中的阶段或记录是否属于当前选中的调试对象。 */
export function isMessageDebugTimelineMarkSelected(selectedTargetKey, groupKey, itemId = null) {
  if (!selectedTargetKey || !groupKey) return false
  if (selectedTargetKey === `turn:${groupKey}`) return true
  if (itemId === null || itemId === undefined) return false
  return selectedTargetKey === `item:${groupKey}:${itemId}`
}

const INSPECTOR_SEPARATOR_SIZE = 4

/** 在主记录区与详情区都可用的范围内约束分栏尺寸。 */
function constrainMessageDebugInspectorSize(
  containerSize,
  requestedSize,
  minimumRecordsSize,
  minimumInspectorSize
) {
  if (!Number.isFinite(containerSize) || containerSize <= 0) return null
  const resolvedRecordsSize = Math.min(
    minimumRecordsSize,
    Math.max(0, containerSize - INSPECTOR_SEPARATOR_SIZE)
  )
  const maximumInspectorSize = Math.max(
    0,
    containerSize - INSPECTOR_SEPARATOR_SIZE - resolvedRecordsSize
  )
  const resolvedMinimumInspectorSize = Math.min(minimumInspectorSize, maximumInspectorSize)
  const fallbackSize = containerSize * 0.42
  const size = Number.isFinite(requestedSize) ? requestedSize : fallbackSize
  return Math.round(Math.max(resolvedMinimumInspectorSize, Math.min(maximumInspectorSize, size)))
}

/** 在记录区与详情区都可用的范围内约束详情面板高度。 */
export function constrainMessageDebugInspectorHeight(containerHeight, requestedHeight) {
  return constrainMessageDebugInspectorSize(containerHeight, requestedHeight, 120, 180)
}

/** 在记录区与详情区都可用的范围内约束宽屏详情面板宽度。 */
export function constrainMessageDebugInspectorWidth(containerWidth, requestedWidth) {
  return constrainMessageDebugInspectorSize(containerWidth, requestedWidth, 300, 260)
}

/** 提取 AI 消息中去重后的工具名称。 */
export function extractMessageToolNames(message) {
  const toolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : []
  return [
    ...new Set(
      toolCalls
        .map((toolCall) => toolCall?.name || toolCall?.tool_name || toolCall?.function?.name)
        .filter(Boolean)
    )
  ]
}

export function getMessageInputId(message, { allowMessageIdFallback = false } = {}) {
  const metadataId = message?.extra_metadata?.input_id
  if (typeof metadataId === 'string' && metadataId.trim()) return metadataId.trim()
  if (typeof message?.input_id === 'string' && message.input_id.trim()) {
    return message.input_id.trim()
  }
  if (allowMessageIdFallback && message?.type === 'human' && typeof message.id === 'string') {
    const messageId = message.id.trim()
    if (messageId) return messageId
  }
  return null
}

export function getMessageRunId(message) {
  const metadataId = message?.extra_metadata?.run_id
  if (typeof metadataId === 'string' && metadataId.trim()) return metadataId.trim()
  if (typeof message?.run_id === 'string' && message.run_id.trim()) return message.run_id.trim()
  return null
}

/** 只用接入响应或派发事件中的明确关联补齐对应用户消息。 */
export function bindMessageInputRun(messages, inputId, runId) {
  if (!inputId || !runId) return
  for (const message of messages || []) {
    if (!['human', 'user'].includes(message.type || message.role)) continue
    if (getMessageInputId(message) !== inputId || getMessageRunId(message)) continue
    message.run_id = runId
    message.extra_metadata = { ...message.extra_metadata, run_id: runId }
  }
}

function auditRole(message) {
  const type = message?.type || message?.role
  if (type === 'ai') return 'assistant'
  return type
}

function aiRoleLabel({ isError, operationId, hasTools }) {
  if (isError) return 'Error'
  if (operationId) return hasTools ? 'Model · Tools' : 'Model'
  return hasTools ? 'AI · Tools' : 'AI'
}

/** 将后端无时区的 PostgreSQL 时间字符串明确规范为 UTC。 */
function normalizePersistedUtcTimestamp(value) {
  if (typeof value !== 'string') return value || null
  const timestamp = value.trim()
  if (!timestamp) return null
  if (!/^\d{4}-\d{2}-\d{2}T/.test(timestamp) || /(Z|[+-]\d{2}:\d{2})$/i.test(timestamp)) {
    return timestamp
  }
  return `${timestamp}Z`
}

/** 保持输入顺序，将原始历史消息转换为调试列表条目。 */
export function buildMessageDebugEntries(messages) {
  const source = Array.isArray(messages) ? messages : []

  return source.map((message, index) => {
    const type = message?.type || message?.role || 'unknown'
    const runId = getMessageRunId(message)
    const inputId = getMessageInputId(message)
    const operationId =
      typeof message?.operation_id === 'string' && message.operation_id.trim()
        ? message.operation_id.trim()
        : null
    const common = {
      inputId,
      id:
        ['human', 'user'].includes(type) && inputId
          ? `input:${inputId}:human`
          : operationId
            ? `${runId || 'unassigned'}:${auditRole(message) || 'unknown'}:${operationId}`
            : String(message?.id ?? `message-${index}`),
      runId,
      turnId: message?.turn_id || null,
      operationId,
      model: message?.extra_metadata?.model || '',
      usage: message?.usage,
      createdAt: normalizePersistedUtcTimestamp(message?.created_at),
      startedAt: normalizePersistedUtcTimestamp(message?.started_at),
      finishedAt: normalizePersistedUtcTimestamp(message?.finished_at),
      durationMs: Number.isFinite(message?.duration_ms) ? message.duration_ms : null,
      sequence: Number.isFinite(message?.sequence) ? message.sequence : null,
      finishedSequence: Number.isFinite(message?.finished_sequence)
        ? message.finished_sequence
        : null,
      executionStatus: message?.execution_status || null,
      raw: message
    }
    const isError = Boolean(message?.error_type || message?.error_message)

    if (type === 'human' || type === 'user') {
      return {
        ...common,
        role: 'human',
        roleLabel: 'User',
        summary: summarizeContent(message.content, 200) || '[用户输入/附件]'
      }
    }

    if (type === 'system') {
      return {
        ...common,
        role: 'system',
        roleLabel: 'System',
        summary: summarizeContent(message.content, 200) || '[系统消息]'
      }
    }

    if (type === 'tool') {
      const toolName = message?.name || message?.tool_name || message?.function?.name
      const content = summarizeContent(message.content, 140)
      const error = summarizeContent(message.error_message, 140)
      const input = summarizeContent(
        message?.tool_input ? JSON.stringify(message.tool_input) : '',
        120
      )
      let auditDetail = ''
      if (error) auditDetail = `错误: ${error}`
      else if (content) auditDetail = `输出: ${content}`
      else if (input) auditDetail = `输入: ${input}`
      const historyDetail = [toolName ? `工具: ${toolName}` : '', content]
        .filter(Boolean)
        .join(' | ')
      return {
        ...common,
        role: 'tool',
        toolName: toolName || '',
        roleLabel: operationId && toolName ? `Tool · ${toolName}` : 'Tool',
        summary: (operationId ? auditDetail : historyDetail) || '[工具执行]'
      }
    }

    if (type === 'ai' || type === 'assistant') {
      const toolNames = extractMessageToolNames(message)
      const content = summarizeContent(message.content, 160)
      const summary = isError
        ? message.error_message || message.error_type || '执行异常'
        : content || (toolNames.length ? `Tool Calls: ${toolNames.join(', ')}` : '[AI 回复]')

      return {
        ...common,
        role: isError ? 'error' : 'ai',
        roleLabel: aiRoleLabel({ isError, operationId, hasTools: toolNames.length > 0 }),
        summary
      }
    }

    return {
      ...common,
      role: 'other',
      roleLabel: String(type),
      summary: summarizeContent(message?.content, 200) || '[未知消息]'
    }
  })
}

/** 格式化后端 monotonic Model/Tool 耗时，不从 wall-clock 推导。 */
export function formatAuditDuration(durationMs) {
  if (!Number.isFinite(durationMs) || durationMs < 0) return ''
  if (durationMs < 1000) return `${durationMs} ms`
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 2 : 1)} s`
  const totalSeconds = Math.round(durationMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

/** 按连续 Run 或待运行请求分组，以请求身份保持接入前后的选择。 */
export function groupMessageDebugEntries(entries) {
  const source = Array.isArray(entries) ? entries : []
  const groups = []
  const inputOccurrences = new Map()

  source.forEach((item) => {
    const runId = typeof item?.runId === 'string' && item.runId.trim() ? item.runId.trim() : null
    let group = groups.at(-1)
    const inputId = item?.inputId || null
    if (!group || group.runId !== runId || (!runId && group.inputId !== inputId)) {
      const occurrence = inputOccurrences.get(inputId) || 0
      if (inputId) inputOccurrences.set(inputId, occurrence + 1)
      group = {
        key: inputId
          ? `input:${inputId}:${occurrence}`
          : `${runId || 'unassigned'}-${groups.length}`,
        inputId,
        runId,
        items: []
      }
      groups.push(group)
    }
    group.items.push(item)
  })

  return groups
}

/** 以 AgentRun 投影补齐没有普通消息或审计记录的 Run 分组。 */
export function mergeMessageDebugRunGroups(entries, runTraces) {
  const entryGroups = groupMessageDebugEntries(entries)
  const entryRunIds = new Set(entryGroups.map((group) => group.runId).filter(Boolean))
  const traceOrder = new Map()
  const missingGroups = []
  const seenRunIds = new Set()
  for (const runTrace of Array.isArray(runTraces) ? runTraces : []) {
    const runId = typeof runTrace?.run_id === 'string' ? runTrace.run_id.trim() : ''
    if (!runId || seenRunIds.has(runId)) continue
    seenRunIds.add(runId)
    const index = traceOrder.size
    traceOrder.set(runId, index)
    if (!entryRunIds.has(runId)) {
      missingGroups.push({ index, key: `${runId}-trace`, runId, items: [] })
    }
  }

  const groups = []
  let nextMissingIndex = 0
  entryGroups.forEach((group) => {
    const groupTraceIndex = traceOrder.get(group.runId)
    while (
      Number.isFinite(groupTraceIndex) &&
      missingGroups[nextMissingIndex]?.index < groupTraceIndex
    ) {
      groups.push(missingGroups[nextMissingIndex])
      nextMissingIndex += 1
    }
    groups.push(group)
  })
  groups.push(...missingGroups.slice(nextMissingIndex))
  return groups
}

/** 将执行段按持久 Turn 归组，保留没有归属的输入与运行。 */
export function groupMessageDebugTurns(runGroups) {
  const groups = []
  const turns = new Map()
  for (const run of runGroups) {
    let group = run.turnId ? turns.get(run.turnId) : null
    if (!group) {
      group = {
        key: run.turnId ? `turn:${run.turnId}` : run.key,
        turnId: run.turnId || null,
        inputId: run.inputId,
        index: groups.length,
        runs: [],
        items: []
      }
      groups.push(group)
      if (run.turnId) turns.set(run.turnId, group)
    }
    group.runs.push(run)
    group.items.push(...run.items)
  }
  return groups
}

const parseEntryTimestamp = (value) => {
  if (typeof value !== 'string' || !value.trim()) return null
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : null
}

/** 读取消息或审计记录自身持久化的绝对时间范围。 */
export function getMessageDebugEntryTimeRange(entry) {
  const startedAt = parseEntryTimestamp(entry?.startedAt || entry?.createdAt)
  const finishedAt = parseEntryTimestamp(entry?.finishedAt)
  if (startedAt === null && finishedAt === null) return null

  const startMs = startedAt ?? finishedAt
  const endMs = finishedAt ?? startMs
  if (endMs < startMs) return null
  return { startMs, endMs }
}

/** 将记录映射到 Run 时间概览；缺少自身时间时保留完整 Run 窗口。 */
export function buildMessageDebugTraceSpans(entries, timelineWindow) {
  if (
    !timelineWindow ||
    !Number.isFinite(timelineWindow.durationMs) ||
    timelineWindow.durationMs <= 0
  )
    return []
  const source = Array.isArray(entries) ? entries : []

  return source.flatMap((entry) => {
    const range = getMessageDebugEntryTimeRange(entry)
    const startOffsetMs = range
      ? Math.max(0, Math.min(timelineWindow.durationMs, range.startMs - timelineWindow.startMs))
      : 0
    const endOffsetMs = range
      ? Math.max(
          startOffsetMs,
          Math.min(timelineWindow.durationMs, range.endMs - timelineWindow.startMs)
        )
      : timelineWindow.durationMs
    return [
      {
        key: entry.id,
        role: entry.role,
        executionStatus: entry.executionStatus,
        timingFallback: !range,
        startOffsetMs,
        endOffsetMs,
        leftPercent: (startOffsetMs / timelineWindow.durationMs) * 100,
        widthPercent: Math.max(
          ((endOffsetMs - startOffsetMs) / timelineWindow.durationMs) * 100,
          0.35
        )
      }
    ]
  })
}

/** 将记录映射到首尾拼接的 Run 时间段，并判断是否与选择范围相交。 */
export function isMessageDebugEntryInTimeRange(
  entry,
  runWindow,
  runOffsetMs,
  timelineDurationMs,
  rangeStart,
  rangeEnd
) {
  if (!runWindow || !Number.isFinite(timelineDurationMs) || timelineDurationMs <= 0) return true
  const range = getMessageDebugEntryTimeRange(entry)
  const projectTimestamp = (timestamp) =>
    Math.max(0, Math.min(runWindow.durationMs, timestamp - runWindow.startMs))
  const entryStart = runOffsetMs + (range ? projectTimestamp(range.startMs) : 0)
  const entryEnd = runOffsetMs + (range ? projectTimestamp(range.endMs) : runWindow.durationMs)
  const selectedStart = timelineDurationMs * rangeStart
  const selectedEnd = timelineDurationMs * rangeEnd
  return entryEnd >= selectedStart && entryStart <= selectedEnd
}
