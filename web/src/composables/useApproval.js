import { reactive } from 'vue'
import { extractPendingInterrupt } from './approvalInterrupt'

export { extractPendingInterrupt }

export function useApproval({ getThreadState, fetchThreadMessages }) {
  const approvalState = reactive({
    showModal: false,
    kind: 'ask_user_question',
    questions: [],
    actionRequests: [],
    reviewConfigs: [],
    status: '',
    threadId: null,
    parentRunId: null
  })

  const applyInterruptToApprovalState = (pendingInterrupt, fallbackThreadId) => {
    approvalState.showModal = true
    approvalState.kind = pendingInterrupt.kind || 'ask_user_question'
    approvalState.questions = pendingInterrupt.questions || []
    approvalState.actionRequests = pendingInterrupt.actionRequests || []
    approvalState.reviewConfigs = pendingInterrupt.reviewConfigs || []
    approvalState.status = pendingInterrupt.status || ''
    approvalState.threadId = pendingInterrupt.threadId || fallbackThreadId
    approvalState.parentRunId = pendingInterrupt.parentRunId || null
  }

  const clearApprovalState = () => {
    approvalState.showModal = false
    approvalState.kind = 'ask_user_question'
    approvalState.questions = []
    approvalState.actionRequests = []
    approvalState.reviewConfigs = []
    approvalState.status = ''
    approvalState.threadId = null
    approvalState.parentRunId = null
  }

  const processApprovalInStream = (chunk, threadId, currentAgentId) => {
    const threadState = getThreadState(threadId)
    if (!threadState) return false

    const pendingInterrupt = extractPendingInterrupt(chunk, threadId)
    if (!pendingInterrupt) return false

    threadState.isStreaming = false
    threadState.pendingInterrupt = pendingInterrupt

    applyInterruptToApprovalState(pendingInterrupt, threadId)

    fetchThreadMessages({ agentId: currentAgentId, threadId })

    return true
  }

  const restoreInterruptFromThreadState = (threadId) => {
    const threadState = getThreadState(threadId)
    const pendingInterrupt = threadState?.pendingInterrupt
    if (!pendingInterrupt) return false
    // 两种 kind 都需要恢复(questions 或 actionRequests 至少一个非空)
    if (!pendingInterrupt.questions?.length && !pendingInterrupt.actionRequests?.length) {
      return false
    }

    threadState.isStreaming = false
    threadState.replyLoadingVisible = false
    threadState.pendingRequestId = null
    applyInterruptToApprovalState(pendingInterrupt, threadId)
    return true
  }

  const hideApprovalState = () => {
    clearApprovalState()
  }

  const resetApprovalState = () => {
    const threadState = getThreadState(approvalState.threadId)
    if (threadState) {
      threadState.pendingInterrupt = null
    }
    clearApprovalState()
  }

  return {
    approvalState,
    processApprovalInStream,
    restoreInterruptFromThreadState,
    hideApprovalState,
    resetApprovalState
  }
}
