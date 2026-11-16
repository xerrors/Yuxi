const createOnGoingConvState = () => ({
  msgChunks: {},
  currentRequestKey: null,
  currentAssistantKey: null,
  toolCallBuffers: {}
})

const IDLE_QUEUE_SNAPSHOT = Object.freeze({
  status: 'idle',
  paused_reason: null,
  blocking_run_id: null,
  can_continue: false
})

export function useAgentThreadState({
  chatState,
  getCurrentThreadId,
  onStopThread = null,
  onBeforeResetThread = null,
  onBeforeCleanupThread = null
}) {
  const resetThreadUiState = (threadState) => {
    if (!threadState) return
    threadState.replyLoadingVisible = false
    threadState.pendingRequestId = null
  }

  const getThreadState = (threadId) => {
    if (!threadId) return null
    if (!chatState.threadStates[threadId]) {
      chatState.threadStates[threadId] = {
        isStreaming: false,
        runStreamAbortController: null,
        activeRunId: null,
        activeRunSteerable: false,
        runLastSeq: '0-0',
        lastRetryableJobTry: null,
        replyLoadingVisible: false,
        pendingRequestId: null,
        pendingInterrupt: null,
        agentStateRequestVersion: 0,
        onGoingConv: createOnGoingConvState(),
        agentState: null,
        contextCompressing: false,
        queuedRequests: [],
        queueSnapshot: { ...IDLE_QUEUE_SNAPSHOT },
        continueQueueInFlight: false,
        requestStreams: {}
      }
    }
    return chatState.threadStates[threadId]
  }

  const stopThreadStream = (threadId) => {
    if (!threadId) return
    if (typeof onStopThread === 'function') {
      onStopThread(threadId)
    }
  }

  const abortAllRequestStreams = (threadState) => {
    if (!threadState?.requestStreams) return
    for (const entry of Object.values(threadState.requestStreams)) {
      entry.controller?.abort()
    }
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
    abortAllRequestStreams(threadState)
    delete chatState.threadStates[threadId]
  }

  const resetOnGoingConv = (threadId = null, { preserveRequestStreams = false } = {}) => {
    const targetThreadId =
      threadId || (typeof getCurrentThreadId === 'function' ? getCurrentThreadId() : null)

    if (targetThreadId) {
      const threadState = getThreadState(targetThreadId)
      if (!threadState) return

      if (typeof onBeforeResetThread === 'function') {
        onBeforeResetThread(targetThreadId)
      }

      if (threadState.runStreamAbortController) {
        threadState.runStreamAbortController.abort()
        threadState.runStreamAbortController = null
      }
      if (!preserveRequestStreams && threadState.requestStreams) {
        abortAllRequestStreams(threadState)
        threadState.requestStreams = {}
      }

      threadState.onGoingConv = createOnGoingConvState()
      resetThreadUiState(threadState)
      return
    }

    Object.keys(chatState.threadStates).forEach((id) => {
      cleanupThreadState(id)
    })
  }

  return {
    getThreadState,
    cleanupThreadState,
    resetOnGoingConv,
    stopThreadStream
  }
}

export { IDLE_QUEUE_SNAPSHOT }
