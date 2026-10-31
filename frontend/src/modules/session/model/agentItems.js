/** 公开 item 的唯一客户端 reducer；协议状态先落地，展示平滑独立处理。 */
export const createItemState = () => ({ items: {}, seenEvents: new Set(), closedParts: new Set() })

const clone = (value) => typeof structuredClone === 'function'
  ? structuredClone(value)
  : JSON.parse(JSON.stringify(value))
const terminal = (item) => item && item.status !== 'in_progress'
const partKey = (event) => `${event.item_id}:${event.content_index}`
const canReplace = (existing, incoming) => !existing || (
  (!['completed', 'failed'].includes(existing.status) || existing.status === incoming.status) &&
  (existing.phase !== 'final_answer' || incoming.phase === 'final_answer')
)

export function mergeItemSnapshot(state, items) {
  for (const item of items || []) {
    const existing = state.items[item.id]
    if (canReplace(existing, item) && (!existing || terminal(item) || !terminal(existing))) {
      if (existing?.status === 'in_progress' && item.status === 'in_progress') {
        // 已提交内容块覆盖漏收的 done；未结束内容保留本地较新的增量。
        for (const index of item.yuxi?.completed_content_indices || []) {
          existing.content[index] = clone(item.content[index])
        }
        for (const index of item.yuxi?.completed_reasoning_indices || []) {
          existing.yuxi.reasoning ||= {}
          existing.yuxi.reasoning[index] = item.yuxi.reasoning[index]
        }
      } else {
        state.items[item.id] = clone(item)
      }
      for (const index of item.yuxi?.completed_content_indices || []) {
        state.closedParts.add(`${item.id}:${index}`)
      }
      for (const index of item.yuxi?.completed_reasoning_indices || []) {
        state.closedParts.add(`reasoning:${item.id}:${index}`)
      }
    }
  }
}

export function applyAgentEvent(state, event) {
  if (!event?.event_id || state.seenEvents.has(event.event_id)) return false
  state.seenEvents.add(event.event_id)
  if (event.type === 'agent.session.turn.item.added') {
    if (!state.items[event.item.id]) state.items[event.item.id] = clone(event.item)
    return true
  }
  if (event.type === 'agent.session.turn.item.done') {
    if (!canReplace(state.items[event.item.id], event.item)) return false
    state.items[event.item.id] = clone(event.item)
    return true
  }
  const item = state.items[event.item_id]
  if (!item || terminal(item)) return false
  const index = event.content_index
  const key = partKey(event)
  if (event.type === 'agent.session.turn.output_text.delta') {
    if (state.closedParts.has(key)) return false
    item.content[index] ||= { type: 'output_text', text: '' }
    item.content[index].text += event.delta
  } else if (event.type === 'agent.session.turn.output_text.done' ||
             event.type === 'agent.session.turn.content_part.done') {
    item.content[index] = clone(event.part || { type: 'output_text', text: event.text })
    state.closedParts.add(key)
  } else if (event.type === 'agent.session.turn.content_part.added') {
    item.content[index] ||= clone(event.part)
  } else if (event.type === 'yuxi.session.turn.reasoning.delta') {
    if (state.closedParts.has(`reasoning:${key}`)) return false
    item.yuxi.reasoning ||= {}
    item.yuxi.reasoning[index] = (item.yuxi.reasoning[index] || '') + event.delta
  } else if (event.type === 'yuxi.session.turn.reasoning.done') {
    item.yuxi.reasoning ||= {}
    item.yuxi.reasoning[index] = event.text
    state.closedParts.add(`reasoning:${key}`)
  }
  return true
}

/** 只投影当前运行仍在产出的思考段，历史与已关闭内容保留在原始 item。 */
export function getStreamingReasoning(state) {
  const active = state.activeReasoning
  if (!state.isStreaming || state.cancellationPending || state.cooperationWaiting ||
      state.pendingInterrupt || !active) return null
  const item = state.itemState.items[active.itemId]
  if (item?.status !== 'in_progress' || item.yuxi?.run_id !== state.activeRunId ||
      state.itemState.closedParts.has(`reasoning:${active.itemId}:${active.contentIndex}`)) return null
  return { itemId: item.id, content: item.yuxi?.reasoning?.[active.contentIndex] || '' }
}

/** 从标准 item 派生消息视图；思考仅接受当前流式展示投影。 */
export function itemsToMessages(items, reasoning = null) {
  const sorted = [...items].sort((left, right) =>
    (left.yuxi?.message_id || 0) - (right.yuxi?.message_id || 0) ||
    (left.yuxi?.output_index || 0) - (right.yuxi?.output_index || 0))
  const outputs = new Map(sorted.filter((item) => item.type === 'function_call_output')
    .map((item) => [`${item.yuxi.run_id}:${item.call_id}`, item]))
  const linkedOutputs = new Map(sorted.filter((item) => item.type === 'function_call_output' && item.yuxi.call_item_id)
    .map((item) => [item.yuxi.call_item_id, item]))
  const messages = new Map()
  for (const item of sorted) {
    if (item.type === 'function_call_output') continue
    // resume 是内部等待点恢复输入，保留协议状态但不展示为用户发言。
    if (item.type === 'message' && item.role === 'user' && item.yuxi?.message_type === 'resume') continue
    const key = `${item.yuxi?.run_id || ''}:${item.yuxi?.message_id || item.id}`
    const runId = item.yuxi?.run_id
    const message = messages.get(key) || {
      id: item.id, type: item.role === 'user' ? 'human' : 'ai', content: '',
      input_origin: item.yuxi?.input_origin,
      run_id: runId, turn_id: item.turn_id, input_id: item.yuxi?.input_id,
      delivery_status: item.yuxi?.delivery_status, created_at: item.yuxi?.created_at,
      execution_status: item.status, message_type: item.yuxi?.message_type,
      extra_metadata: { input_id: item.yuxi?.input_id, run_id: runId,
                        waiting_kind: item.yuxi?.waiting_kind,
                        attachments: item.yuxi?.attachments || [] }, tool_calls: []
    }
    if (item.type === 'message') {
      message.id = item.id
      message.content = item.content.filter((part) => part.type === 'output_text' || part.type === 'input_text')
        .map((part) => part.text).join('')
      message.reasoning_content = item.id === reasoning?.itemId ? reasoning.content : ''
      message.image_urls = item.content.filter((part) => part.type === 'input_image')
        .map((part) => part.image_url)
      message.phase = item.phase
    } else if (item.type === 'function_call') {
      const output = linkedOutputs.get(item.id) || outputs.get(`${runId}:${item.call_id}`)
      message.tool_calls.push({
        id: item.call_id, name: item.name, args: item.arguments,
        status: item.status === 'failed' || output?.status === 'failed' ? 'error' :
          output?.status === 'completed' || item.status === 'completed' ? 'success' :
          item.yuxi?.waiting_kind === 'cooperation' && item.status === 'incomplete' ? 'waiting' :
          item.status === 'incomplete' ? 'incomplete' : 'pending',
        error_message: output?.error,
        tool_call_result: output && output.status !== 'in_progress'
          ? { content: output.output || output.error || '', tool_call_id: item.call_id, run_id: runId } : null
      })
    }
    messages.set(key, message)
  }
  return [...messages.values()]
}
