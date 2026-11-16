import MessageProcessor from '@/utils/messageProcessor'
import { enrichTaskToolCalls } from '@/components/ToolCallingResult/toolRegistry'
import { collapseConversationProcess } from '@/utils/conversationProcessGrouping'

const hasVisibleAssistantBody = (message) => {
  if (!message || message.type !== 'ai') return true

  const { content } = MessageProcessor.parseAssistantMessageBody(message)
  return Boolean(
    content || message.error_type || message.extra_metadata?.error_type || message.isStoppedByUser
  )
}

const defaultEnrichToolCalls = (message) => enrichTaskToolCalls(message?.tool_calls)

/** 将相邻推理和工具调用按顺序归组，正文保持独立。 */
export const getConversationDisplayItems = (
  conv,
  { enrichToolCalls = defaultEnrichToolCalls, collapseIntermediate = false, runTiming = null } = {}
) => {
  if (!Array.isArray(conv?.messages) || conv.messages.length === 0) return []

  const items = []
  let pendingToolGroup = null

  const flushToolGroup = () => {
    if (pendingToolGroup && pendingToolGroup.entries.length > 0) {
      items.push(pendingToolGroup)
    }
    pendingToolGroup = null
  }

  conv.messages.forEach((message, index) => {
    if (message.type !== 'ai') {
      flushToolGroup()
      items.push({
        type: 'message',
        key: message.id || `message-${index}`,
        message,
        sourceIndex: index
      })
      return
    }

    const { reasoningContent } = MessageProcessor.parseAssistantMessageBody(message)
    const toolCalls = enrichToolCalls(message)

    /** 为当前连续处理过程保留稳定的分组标识。 */
    const ensureToolGroup = (segment) => {
      if (!pendingToolGroup) {
        pendingToolGroup = {
          type: 'tool-group',
          key: `tool-group-${message.id || index}-${segment}`,
          toolCalls: [],
          entries: []
        }
      }
      return pendingToolGroup
    }

    if (reasoningContent) {
      ensureToolGroup('reasoning').entries.push({
        type: 'reasoning',
        key: `reasoning-${message.id || index}`,
        content: reasoningContent
      })
    }

    if (hasVisibleAssistantBody(message)) {
      flushToolGroup()
      items.push({
        type: 'message',
        key: message.id || `message-${index}`,
        message: reasoningContent ? { ...message, reasoning_content: '' } : message,
        sourceIndex: index
      })
    }

    if (toolCalls.length > 0) {
      const group = ensureToolGroup('tools')
      group.toolCalls.push(...toolCalls)
      group.entries.push(
        ...toolCalls.map((toolCall, toolIndex) => ({
          type: 'tool',
          key: `tool-${message.id || index}-${toolCall.id || toolIndex}`,
          toolCall
        }))
      )
    }
  })

  flushToolGroup()
  return collapseConversationProcess(items, collapseIntermediate, runTiming)
}
