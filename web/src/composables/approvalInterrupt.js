import { normalizeQuestions } from '../utils/questionUtils.js'

const APPROVAL_REQUIRED_STATUSES = new Set([
  'ask_user_question_required',
  'human_approval_required'
])

/**
 * 从流式 chunk 提取待处理的 interrupt。
 *
 * 两类 interrupt:
 * - ask_user_question: 走 questions 通道(选项式问询)
 * - human_approval: 走 action_requests 通道(HumanInTheLoopMiddleware 工具审批)
 *
 * 返回 null 表示该 chunk 不是可处理的 interrupt(或 payload 缺关键字段)。
 */
export const extractPendingInterrupt = (chunk, threadId) => {
  const status = chunk?.status || ''
  if (!APPROVAL_REQUIRED_STATUSES.has(status)) return null

  const fallbackThreadId = chunk?.thread_id || threadId
  const parentRunId = chunk?.run_id || chunk?.parent_run_id || null
  const interruptInfo = chunk?.interrupt_info || {}

  if (status === 'human_approval_required') {
    const actionRequests = chunk?.action_requests || interruptInfo?.action_requests || []
    const reviewConfigs = chunk?.review_configs || interruptInfo?.review_configs || []
    const list = Array.isArray(actionRequests) ? actionRequests : []
    if (!list.length) return null
    return {
      kind: 'human_approval',
      actionRequests: list,
      reviewConfigs: Array.isArray(reviewConfigs) ? reviewConfigs : [],
      status,
      threadId: fallbackThreadId,
      parentRunId
    }
  }

  const rawQuestions = chunk?.questions || interruptInfo?.questions || []
  const questions = normalizeQuestions(rawQuestions)
  if (!questions.length) return null

  return {
    kind: 'ask_user_question',
    questions,
    source: chunk?.source || interruptInfo?.source || 'interrupt',
    status,
    threadId: fallbackThreadId,
    parentRunId
  }
}
