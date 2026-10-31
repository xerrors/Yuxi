import { getToolCallId, getToolCallStatus, parseToolCallResult } from './toolCallProjection.js'

/** 主对话投影用户输入、成功发送的消息和新建会话入口，完整轨迹保留原始消息。 */
export function personalMessages(messages) {
  return messages.flatMap((message) => {
    if (message.type === 'human') return [message]
    if (message.type !== 'ai') return []
    return (message.tool_calls || []).flatMap((call) => {
      const name = getToolCallId(call)
      if (!['send_to_user', 'create_session'].includes(name) || !call.tool_call_result ||
          getToolCallStatus(call) !== 'completed') return []
      const result = parseToolCallResult(call)
      const projected = { ...message, reasoning_content: '', tool_calls: [], phase: 'final_answer' }
      if (name === 'create_session') {
        if (typeof result?.session_id !== 'string' || !result.session_id.trim()) return []
        return [{
          ...projected, id: `${message.id}:session:${call.id}`, type: 'created-session', content: '',
          session: { id: result.session_id, name: result.name || '新对话' }
        }]
      }
      if (typeof result?.content !== 'string' || !result.content.trim()) return []
      return [{ ...projected, id: `${message.id}:send:${call.id}`, content: result.content }]
    })
  })
}
