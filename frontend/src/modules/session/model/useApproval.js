import { reactive } from 'vue'
import { hasPendingInterruptPayload } from '@/modules/session/model/toolApproval'

const extractQuestionPayload = (event) => {
  const interruptInfo = event?.interrupt_info || {}
  const rawQuestions = event?.questions || interruptInfo?.questions || []
  const source = event?.source || interruptInfo?.source || 'interrupt'
  const questions = Array.isArray(rawQuestions) ? rawQuestions : []

  return {
    questions,
    source
  }
}

const extractToolApprovalPayload = (event) => {
  const approval = event?.approval || event?.interrupt_info?.approval || {}
  const actionRequests = Array.isArray(approval.action_requests) ? approval.action_requests : []
  const reviewConfigs = Array.isArray(approval.review_configs) ? approval.review_configs : []
  // action_requests 与 review_configs 一一对应，前端只消费 action_requests
  if (!actionRequests.length || actionRequests.length !== reviewConfigs.length) return null
  return { actionRequests }
}

export const extractPendingInterrupt = (event, threadId) => {
  if (event?.status === 'human_approval_required') {
    const approval = extractToolApprovalPayload(event)
    if (!approval) return null
    return {
      kind: 'tool_approval',
      ...approval,
      status: event.status,
      threadId: event?.thread_id || threadId,
      interruptedRunId: event?.run_id || null
    }
  }
  const payload = extractQuestionPayload(event)
  if (!payload.questions.length) return null

  return {
    kind: 'question',
    questions: payload.questions,
    source: payload.source,
    status: event?.status || '',
    threadId: event?.thread_id || threadId,
    interruptedRunId: event?.run_id || null
  }
}

/** 从持久 Turn 等待点恢复可提交的审批界面。 */
export const pendingInterruptFromWaitpoint = (waitpoint, threadId) => {
  if (!waitpoint?.id || !waitpoint.run_id) return null
  if (waitpoint.kind === 'approval' && waitpoint.calls?.length) {
    return {
      kind: 'tool_approval',
      actionRequests: waitpoint.calls,
      status: 'human_approval_required',
      threadId,
      interruptedRunId: waitpoint.run_id,
      waitpointId: waitpoint.id
    }
  }
  const questions = Array.isArray(waitpoint.questions) ? waitpoint.questions : []
  if (waitpoint.kind === 'answer' && questions.length) {
    return {
      kind: 'question',
      questions,
      status: 'ask_user_question_required',
      threadId,
      interruptedRunId: waitpoint.run_id,
      waitpointId: waitpoint.id
    }
  }
  return null
}

export function useApproval({ getThreadState }) {
  const approvalState = reactive({
    showModal: false,
    questions: [],
    kind: '',
    actionRequests: [],
    status: '',
    threadId: null,
    interruptedRunId: null,
    waitpointId: null
  })

  const applyInterruptToApprovalState = (pendingInterrupt, fallbackThreadId) => {
    approvalState.showModal = true
    approvalState.questions = pendingInterrupt.questions || []
    approvalState.kind = pendingInterrupt.kind || 'question'
    approvalState.actionRequests = pendingInterrupt.actionRequests || []
    approvalState.status = pendingInterrupt.status || ''
    approvalState.threadId = pendingInterrupt.threadId || fallbackThreadId
    approvalState.interruptedRunId = pendingInterrupt.interruptedRunId || null
    approvalState.waitpointId = pendingInterrupt.waitpointId || null
  }

  const clearApprovalState = () => {
    approvalState.showModal = false
    approvalState.questions = []
    approvalState.kind = ''
    approvalState.actionRequests = []
    approvalState.status = ''
    approvalState.threadId = null
    approvalState.interruptedRunId = null
    approvalState.waitpointId = null
  }

  const restoreInterruptFromThreadState = (threadId) => {
    const threadState = getThreadState(threadId)
    const pendingInterrupt = threadState?.pendingInterrupt
    if (!hasPendingInterruptPayload(pendingInterrupt)) return false

    threadState.isStreaming = false
    threadState.pendingInputId = null
    applyInterruptToApprovalState(pendingInterrupt, threadId)
    return true
  }

  const hideApprovalState = () => {
    clearApprovalState()
  }

  return {
    approvalState,
    restoreInterruptFromThreadState,
    hideApprovalState
  }
}
