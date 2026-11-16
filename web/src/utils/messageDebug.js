/** 将消息内容压缩为单行摘要。 */
function summarizeContent(content, limit) {
  if (typeof content !== 'string') return ''
  return content.replace(/\s+/g, ' ').trim().slice(0, limit)
}

/** 将消息正文转换为适合调试概览忠实展示的文本。 */
export function formatMessageDebugContent(content) {
  if (typeof content === 'string') return content
  if (content === undefined || content === null) return ''
  try {
    return JSON.stringify(content, null, 2)
  } catch {
    return String(content)
  }
}

/** 判断时间概览中的阶段或记录是否属于当前选中的调试对象。 */
export function isMessageDebugTimelineMarkSelected(selectedTargetKey, groupKey, itemId = null) {
  if (!selectedTargetKey || !groupKey) return false
  if (selectedTargetKey === `run:${groupKey}`) return true
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

export function getMessageRequestId(message, { allowMessageIdFallback = false } = {}) {
  const metadataId = message?.extra_metadata?.request_id
  if (typeof metadataId === 'string' && metadataId.trim()) return metadataId.trim()
  if (typeof message?.request_id === 'string' && message.request_id.trim()) {
    return message.request_id.trim()
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
export function bindMessageRequestRun(messages, requestId, runId) {
  if (!requestId || !runId) return
  for (const message of messages || []) {
    if (!['human', 'user'].includes(message.type || message.role)) continue
    if (getMessageRequestId(message) !== requestId || getMessageRunId(message)) continue
    message.run_id = runId
    message.extra_metadata = { ...message.extra_metadata, run_id: runId }
  }
}

/** 合并普通历史与实时投影；只按已有稳定 ID 去重，不按 AI 位置猜测。 */
export function mergeMessageDebugMessages(history, ongoing, requests = []) {
  const persisted = Array.isArray(history) ? history : []
  const live = [
    ...(Array.isArray(ongoing) ? ongoing : []),
    ...requests
      .filter(
        (request) =>
          !(ongoing || []).some((message) => getMessageRequestId(message) === request.request_id)
      )
      .map((request) => ({
        id: request.input_message_id || request.request_id,
        type: 'human',
        request_id: request.request_id,
        content: request.content,
        created_at: request.created_at,
        delivery_status: request.status
      }))
  ]
  const liveHumanByRequestId = new Map(
    live
      .filter((message) => message?.type === 'human')
      .map((message) => [getMessageRequestId(message), message])
      .filter(([requestId]) => requestId)
  )
  const mergedPersisted = persisted.map((message) => {
    if (message?.type !== 'human' || getMessageRunId(message)) return message
    const liveMessage = liveHumanByRequestId.get(getMessageRequestId(message))
    const liveRunId = getMessageRunId(liveMessage)
    if (!liveRunId) return message
    return {
      ...message,
      run_id: liveRunId,
      extra_metadata: {
        ...(message.extra_metadata || {}),
        run_id: liveRunId
      }
    }
  })
  const persistedIds = new Set(mergedPersisted.map((message) => String(message?.id ?? '')))
  const persistedRequestIds = new Set(
    mergedPersisted
      .filter((message) => message?.type === 'human')
      .map(getMessageRequestId)
      .filter(Boolean)
  )
  const pending = live.filter((message) => {
    if (persistedIds.has(String(message?.id ?? ''))) return false
    return message?.type !== 'human' || !persistedRequestIds.has(getMessageRequestId(message))
  })
  return [...mergedPersisted, ...pending]
}

function auditRole(message) {
  const type = message?.type || message?.role
  if (type === 'ai') return 'assistant'
  return type
}

function auditKey(message) {
  const runId = getMessageRunId(message)
  const operationId =
    typeof message?.operation_id === 'string' && message.operation_id.trim()
      ? message.operation_id.trim()
      : null
  const role = auditRole(message)
  return runId && role && operationId ? `${runId}\u0000${role}\u0000${operationId}` : null
}

function liveModelAuditKey(message) {
  const runId = getMessageRunId(message)
  const messageId = typeof message?.id === 'string' && message.id.trim() ? message.id.trim() : null
  const isAi = message?.type === 'ai' || message?.role === 'assistant'
  return runId && messageId && isAi ? `${runId}\u0000assistant\u0000${messageId}` : null
}

function mergeAuditMessage(message, audit, previous = null) {
  const content =
    (typeof message?.content === 'string' && message.content) || previous?.content || audit.content
  const messageToolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : []
  const previousToolCalls = Array.isArray(previous?.tool_calls) ? previous.tool_calls : []
  let toolCalls = Array.isArray(audit.tool_calls) ? audit.tool_calls : []
  if (previousToolCalls.length) toolCalls = previousToolCalls
  if (messageToolCalls.length) toolCalls = messageToolCalls
  return {
    ...(previous || {}),
    ...message,
    ...audit,
    content,
    tool_calls: toolCalls
  }
}

/** 按 Message ID 或 (run_id, role, operation_id) 合并 PG 审计与实时投影。 */
export function mergeMessageDebugAudits(messages, audits) {
  const source = Array.isArray(messages) ? messages : []
  const persistedAudits = Array.isArray(audits) ? audits : []
  if (persistedAudits.length === 0) return source

  const auditsByMessageId = new Map()
  const auditsByOperation = new Map()
  persistedAudits.forEach((audit) => {
    if (audit?.id !== undefined && audit?.id !== null) {
      auditsByMessageId.set(String(audit.id), audit)
    }
    const operationKey = auditKey(audit)
    if (operationKey) auditsByOperation.set(operationKey, audit)
  })

  const merged = []
  const matchedAuditIndexes = new Map()
  source.forEach((message) => {
    const messageId = message?.id === undefined || message?.id === null ? null : String(message.id)
    const operationKey = auditKey(message)
    const liveOperationKey = liveModelAuditKey(message)
    const audit =
      (messageId && auditsByMessageId.get(messageId)) ||
      (operationKey && auditsByOperation.get(operationKey)) ||
      (liveOperationKey && auditsByOperation.get(liveOperationKey))
    if (!audit) {
      merged.push(message)
      return
    }

    const matchedIndex = matchedAuditIndexes.get(audit)
    if (matchedIndex !== undefined) {
      merged[matchedIndex] = mergeAuditMessage(message, audit, merged[matchedIndex])
      return
    }

    matchedAuditIndexes.set(audit, merged.length)
    merged.push(mergeAuditMessage(message, audit))
  })

  const pending = persistedAudits.filter((audit) => !matchedAuditIndexes.has(audit))
  const pendingByRun = new Map()
  pending.forEach((audit) => {
    const runId = getMessageRunId(audit)
    const runAudits = pendingByRun.get(runId) || []
    runAudits.push(audit)
    pendingByRun.set(runId, runAudits)
  })

  const lastMessageIndexByRun = new Map()
  merged.forEach((message, index) => lastMessageIndexByRun.set(getMessageRunId(message), index))

  const nextAuditIndexByRun = new Map()
  const emittedAudits = new Set()
  const ordered = []
  merged.forEach((message, messageIndex) => {
    const runId = getMessageRunId(message)
    const runAudits = pendingByRun.get(runId) || []
    let nextAuditIndex = nextAuditIndexByRun.get(runId) || 0
    const messageSequence = Number.isFinite(message?.sequence)
      ? message.sequence
      : Number.MAX_SAFE_INTEGER
    const isUnmatchedLiveModel = Boolean(liveModelAuditKey(message) && !auditKey(message))
    const hasPersistedOperation = Boolean(auditKey(message))
    while (nextAuditIndex < runAudits.length) {
      const audit = runAudits[nextAuditIndex]
      const auditSequence = Number.isFinite(audit?.sequence)
        ? audit.sequence
        : Number.MAX_SAFE_INTEGER
      if (!isUnmatchedLiveModel && (!hasPersistedOperation || auditSequence >= messageSequence))
        break
      ordered.push(audit)
      emittedAudits.add(audit)
      nextAuditIndex += 1
    }
    ordered.push(message)

    if (lastMessageIndexByRun.get(runId) === messageIndex) {
      while (nextAuditIndex < runAudits.length) {
        const audit = runAudits[nextAuditIndex]
        ordered.push(audit)
        emittedAudits.add(audit)
        nextAuditIndex += 1
      }
    }
    nextAuditIndexByRun.set(runId, nextAuditIndex)
  })

  pending.forEach((audit) => {
    if (!emittedAudits.has(audit)) ordered.push(audit)
  })
  return ordered
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
    const requestId = getMessageRequestId(message)
    const operationId =
      typeof message?.operation_id === 'string' && message.operation_id.trim()
        ? message.operation_id.trim()
        : null
    const common = {
      requestId,
      id:
        ['human', 'user'].includes(type) && requestId
          ? `request:${requestId}:human`
          : operationId
            ? `${runId || 'unassigned'}:${auditRole(message) || 'unknown'}:${operationId}`
            : String(message?.id ?? `message-${index}`),
      runId,
      operationId,
      model: message?.model || message?.extra_metadata?.model || '',
      usage: message?.usage || message?.extra_metadata?.usage,
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
        : [content, toolNames.length ? `工具: ${toolNames.join('、')}` : '']
            .filter(Boolean)
            .join(' | ') || '[AI 回复]'

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

/** 从后端响应中读取可安全打开的 Langfuse HTTP(S) 地址。 */
export function resolveLangfuseRunUrl(payload) {
  if (payload?.available !== true || typeof payload?.url !== 'string') return null

  const value = payload.url.trim()
  if (!value) return null
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? value : null
  } catch {
    return null
  }
}

/** 按连续 Run 或待运行请求分组，以请求身份保持接入前后的选择。 */
export function groupMessageDebugEntries(entries) {
  const source = Array.isArray(entries) ? entries : []
  const groups = []
  const requestOccurrences = new Map()

  source.forEach((item) => {
    const runId = typeof item?.runId === 'string' && item.runId.trim() ? item.runId.trim() : null
    let group = groups.at(-1)
    const requestId = item?.requestId || null
    if (!group || group.runId !== runId || (!runId && group.requestId !== requestId)) {
      const occurrence = requestOccurrences.get(requestId) || 0
      if (requestId) requestOccurrences.set(requestId, occurrence + 1)
      group = {
        key: requestId
          ? `request:${requestId}:${occurrence}`
          : `${runId || 'unassigned'}-${groups.length}`,
        requestId,
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
