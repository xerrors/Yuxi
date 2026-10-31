import { createItemState } from './agentItems.js'

const IDLE_QUEUE_SNAPSHOT = Object.freeze({
  status: 'ready',
  queue_paused: false
})

export function useAgentThreadState({
  chatState,
  onBeforeResetThread = null,
  onBeforeCleanupThread = null
}) {
  const resetThreadUiState = (threadState) => {
    if (!threadState) return
    threadState.replyLoadingVisible = false
    threadState.pendingInputId = null
    threadState.activeReasoning = null
    threadState.hiddenReasoningParts.clear()
  }

  const getThreadState = (threadId) => {
    if (!threadId) return null
    if (!chatState.threadStates[threadId]) {
      chatState.threadStates[threadId] = {
        displayText: {},
        isStreaming: false,
        cancellationPending: false,
        cooperationWaiting: false,
        approvalSubmitting: false,
        runStateVersion: 0,
        runStreamAbortController: null,
        runReconnectTimer: null,
        runReconnectAttempts: 0,
        activeRunId: null,
        activeReasoning: null,
        hiddenReasoningParts: new Set(),
        currentTurnId: null,
        turnStatus: null,
        activeRunSteerable: false,
        threadCursor: null,
        replyLoadingVisible: false,
        pendingInputId: null,
        pendingInterrupt: null,
        agentStateRequestVersion: 0,
        itemState: createItemState(),
        optimisticInputs: {},
        historyLoaded: false,
        agentState: null,
        contextCompressing: false,
        queuedInputs: [],
        queueSnapshot: { ...IDLE_QUEUE_SNAPSHOT },
        continueQueueInFlight: false,
        inputQueueMonitor: null
      }
    }
    return chatState.threadStates[threadId]
  }

  const abortAllInputMonitors = (threadState) => {
    if (!threadState) return
    clearTimeout(threadState.inputQueueMonitor?.timer)
    threadState.inputQueueMonitor?.controller?.abort()
    threadState.inputQueueMonitor = null
  }

  const cleanupThreadState = (threadId) => {
    if (!threadId) return
    const threadState = chatState.threadStates[threadId]
    if (!threadState) return

    if (typeof onBeforeCleanupThread === 'function') {
      onBeforeCleanupThread(threadId)
    }

    if (threadState.runStreamAbortController) {
      threadState.runStreamAbortController.abort()
    }
    clearTimeout(threadState.runReconnectTimer)
    abortAllInputMonitors(threadState)
    delete chatState.threadStates[threadId]
  }

  const resetThreadExecution = (threadId, { preserveInputMonitors = false } = {}) => {
    const threadState = getThreadState(threadId)
    if (!threadState) return

    if (typeof onBeforeResetThread === 'function') {
      onBeforeResetThread(threadId)
    }

    if (threadState.runStreamAbortController) {
      threadState.runStreamAbortController.abort()
      threadState.runStreamAbortController = null
    }
    clearTimeout(threadState.runReconnectTimer)
    threadState.runReconnectTimer = null
    if (!preserveInputMonitors) {
      abortAllInputMonitors(threadState)
    }

    threadState.optimisticInputs = {}
    resetThreadUiState(threadState)
  }

  return {
    getThreadState,
    cleanupThreadState,
    resetThreadExecution
  }
}

export { IDLE_QUEUE_SNAPSHOT }
