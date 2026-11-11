import { agentApi } from '@/apis'
import { parseMentionText } from './mention_utils.js'

/** 生成提交身份，同一份失败请求的重试复用该身份。 */
export function createInputRequestId() {
  return globalThis.crypto?.randomUUID?.() || `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/** 拥有输入准备、回执、乐观展示和重试；编辑器通过生命周期回调消费草稿。 */
export function createSessionInput({ runtime, onPrepared, onAccepted, onRejected }) {
  const send = async (threadId, draft, { retry = false } = {}) => {
    const original = runtime.pendingSends[threadId]
    if (original?.submitting || original && !retry || retry && !original) return false
    const frozen = original || {
      ...draft,
      images: draft.images.map((item) => ({ ...item })),
      attachments: draft.attachments.map((item) => ({ ...item })),
      data: {
        query: draft.text,
        mcps: [...new Set(parseMentionText(draft.text)
          .filter((segment) => segment.kind === 'mention' && segment.type === 'mcp')
          .map((segment) => segment.value))],
        idempotency_key: createInputRequestId(),
        attachment_file_ids: draft.attachments.map((item) => item.id),
        image_content: draft.images.length ? draft.images.map((item) => item.imageUrl) : null,
        model_spec: draft.modelSpec,
        tool_approval_mode: draft.toolApprovalMode,
        mode: draft.mode
      }
    }
    const { text, images, attachments, data } = frozen
    const state = runtime.getThreadState(threadId)
    const hadActiveRun = Boolean(state.activeRunId && (state.isStreaming || state.cooperationWaiting))
    const clientKey = data.idempotency_key
    const message = {
      id: clientKey, type: 'human', role: 'user', content: text,
      created_at: new Date().toISOString(), delivery_status: 'sending',
      message_type: images.length ? 'multimodal_image' : 'text',
      image_urls: images.map((item) => item.imageUrl),
      extra_metadata: { input_id: clientKey, attachments }
    }
    state.pendingInterrupt = null
    if (!hadActiveRun) {
      runtime.resetThreadExecution(threadId)
      state.pendingInputId = clientKey
      state.optimisticInputs[clientKey] = [message]
      state.isStreaming = true
    } else {
      state.queuedInputs.push({ input_id: clientKey, status: 'sending', content: text, message })
    }
    frozen.submitting = true
    runtime.pendingSends[threadId] = frozen
    if (!original) onPrepared?.(threadId, frozen)
    let accepted = false
    try {
      let receipt
      if (retry) {
        try {
          receipt = await agentApi.getSessionReceipt(threadId, clientKey)
        } catch (error) {
          if (error.status !== 404) throw error
        }
      }
      receipt ||= await agentApi.sendThreadMessage(threadId, data)
      if (!receipt.input_id) throw new Error('Public API 未返回 input_id')
      accepted = true
      delete runtime.pendingSends[threadId]
      const inputId = receipt.input_id
      const acceptedMessage = {
        ...message, id: inputId, delivery_status: 'queued',
        extra_metadata: { input_id: inputId, attachments: attachments.map((item) => ({ ...item, input_id: inputId })) }
      }
      delete state.optimisticInputs[clientKey]
      state.queuedInputs = state.queuedInputs.filter((item) => item.input_id !== clientKey)
      state.queuedInputs.push({ input_id: inputId, status: 'pending', kind: data.mode,
        content: text, created_at: message.created_at, message: acceptedMessage })
      if (!hadActiveRun) {
        // 接收回执尚未生成正式消息，保留带 queued 状态的展示直到消费快照接管。
        state.optimisticInputs[inputId] = [acceptedMessage]
        state.pendingInputId = inputId
        state.isStreaming = false
      }
      runtime.startInputMonitor(threadId, inputId)
      await onAccepted?.(threadId, frozen)
      return true
    } catch (error) {
      const rejected = !accepted && error.status >= 400 && error.status < 500 && error.status !== 429
      if (rejected) delete runtime.pendingSends[threadId]
      if (!accepted && runtime.isCurrentThreadState(threadId, state)) {
        state.queuedInputs = state.queuedInputs.filter((item) => item.input_id !== clientKey)
        delete state.optimisticInputs[clientKey]
        if (!hadActiveRun) {
          state.isStreaming = false
          state.pendingInputId = null
          runtime.resetThreadExecution(threadId)
        }
      }
      await onRejected?.(threadId, frozen, error, { accepted, restore: Boolean(rejected) })
      return false
    } finally {
      const pending = runtime.pendingSends[threadId]
      if (pending?.data.idempotency_key === clientKey) pending.submitting = false
    }
  }
  return { send }
}
