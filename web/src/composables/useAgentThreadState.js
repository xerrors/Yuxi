const createOnGoingConvState = () => ({
  msgChunks: {},
  currentRequestKey: null,
  currentAssistantKey: null,
  toolCallBuffers: {}
})

export function useAgentThreadState({ chatState, getCurrentThreadId }) {
  const getThreadState = (threadId) => {
    if (!threadId) return null
    if (!chatState.threadStates[threadId]) {
      chatState.threadStates[threadId] = {
        isStreaming: false,
        streamAbortController: null,
        runStreamAbortController: null,
        activeRunId: null,
        runLastSeq: '0',
        lastRetryableJobTry: null,
        onGoingConv: createOnGoingConvState(),
        agentState: null
      }
    }
    return chatState.threadStates[threadId]
  }

  const stopThreadStream = (threadId) => {
    if (!threadId) return
    const threadState = chatState.threadStates[threadId]
    if (!threadState?.streamAbortController) return

    threadState.streamAbortController.abort()
    threadState.streamAbortController = null
    threadState.isStreaming = false
  }

  const cleanupThreadState = (threadId) => {
    if (!threadId) return
    const threadState = chatState.threadStates[threadId]
    if (!threadState) return

    if (threadState.streamAbortController) {
      threadState.streamAbortController.abort()
    }
    if (threadState.runStreamAbortController) {
      threadState.runStreamAbortController.abort()
    }
    delete chatState.threadStates[threadId]
  }

  const resetOnGoingConv = (threadId = null) => {
    const targetThreadId =
      threadId || (typeof getCurrentThreadId === 'function' ? getCurrentThreadId() : null)

    if (targetThreadId) {
      const threadState = getThreadState(targetThreadId)
      if (!threadState) return

      if (threadState.streamAbortController) {
        threadState.streamAbortController.abort()
        threadState.streamAbortController = null
      }
      if (threadState.runStreamAbortController) {
        threadState.runStreamAbortController.abort()
        threadState.runStreamAbortController = null
      }

      threadState.onGoingConv = createOnGoingConvState()
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
