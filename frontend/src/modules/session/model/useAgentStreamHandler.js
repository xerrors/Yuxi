import { message } from 'ant-design-vue'
import { applyAgentEvent } from '@/modules/session/model/agentItems'

/** 主、子 Thread 共用公开事件消费入口。 */
export function useAgentStreamHandler({
  getThreadState,
  processApprovalInStream,
  streamSmoother
}) {
  const handlePublicEvent = (event, threadId) => {
    if (event.session_id !== threadId) return false
    const state = getThreadState(threadId)
    if (!state) return false
    const type = event.type
    if (
      type === 'yuxi.session.turn.state' &&
      state.activeRunId &&
      event.yuxi?.run_id !== state.activeRunId
    )
      return false
    const itemAlreadyKnown = event.item && state.itemState.items[event.item.id]
    const applied = applyAgentEvent(state.itemState, event)
    const item = state.itemState.items[event.item_id || event.item?.id]
    if (applied && item?.yuxi?.run_id === state.activeRunId) {
      const active = state.activeReasoning
      if (type === 'yuxi.session.turn.reasoning.delta' && event.delta) {
        if (!state.hiddenReasoningParts.has(`${item.id}:${event.content_index}`)) {
          if (active && (active.itemId !== item.id || active.contentIndex !== event.content_index)) {
            state.hiddenReasoningParts.add(`${active.itemId}:${active.contentIndex}`)
          }
          state.activeReasoning = { itemId: item.id, contentIndex: event.content_index }
        }
      } else if (
        (type === 'agent.session.turn.output_text.delta' && event.delta) ||
        type === 'agent.session.turn.output_text.done' ||
        (['agent.session.turn.content_part.added', 'agent.session.turn.content_part.done'].includes(type) &&
          event.part?.type === 'output_text') ||
        (type === 'agent.session.turn.item.added' && item.type === 'function_call' && !itemAlreadyKnown)
      ) {
        // 展示结束不关闭协议内容块，迟到增量仍由 reducer 完整保留。
        if (active) state.hiddenReasoningParts.add(`${active.itemId}:${active.contentIndex}`)
        state.activeReasoning = null
      }
    }
    if (type.endsWith('output_text.delta') || type.endsWith('reasoning.delta')) {
      state.replyLoadingVisible = false
      streamSmoother?.updateText(event.item_id, threadId)
    } else if (type.endsWith('.done')) {
      streamSmoother?.flushThread(threadId)
    }
    if (type === 'agent.session.turn.failed') {
      message.error(event.turn?.error?.message || '本次运行失败，请检查模型配置后重试')
    }
    if (type === 'yuxi.session.turn.state') {
      state.agentStateRequestVersion = (state.agentStateRequestVersion || 0) + 1
      state.agentState = event.agent_state
    } else if (type === 'yuxi.session.turn.context_compression') {
      state.contextCompressing = event.compression?.status === 'started'
    } else if (type === 'yuxi.session.turn.waiting') {
      state.currentTurnId = event.turn_id
      state.activeRunId = event.yuxi.run_id
      state.turnStatus = event.turn.status
      state.replyLoadingVisible = false
      return processApprovalInStream?.(event, threadId) || false
    }
    return false
  }
  return { handlePublicEvent }
}
