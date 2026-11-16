import { agentApi } from '@/apis'
import { processRunSseResponse } from '@/composables/useAgentRunStream'
import { IDLE_QUEUE_SNAPSHOT } from '@/composables/useAgentThreadState'
import { handleChatError } from '@/utils/errorHandler'

export function useAgentRequestQueue({
  getThreadState,
  resetOnGoingConv,
  startRunStream,
  onStreamError
}) {
  const removeRequestFromQueue = (ts, requestId) => {
    if (!ts || !ts.queuedRequests) return
    ts.queuedRequests = ts.queuedRequests.filter((r) => r.request_id !== requestId)
  }

  const stopRequestStream = (threadId, requestId) => {
    const ts = getThreadState(threadId)
    const entry = ts?.requestStreams?.[requestId]
    if (!entry) return
    entry.controller?.abort()
    delete ts.requestStreams[requestId]
  }

  const stopAllRequestStreams = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts?.requestStreams) return
    for (const rid of Object.keys(ts.requestStreams)) {
      stopRequestStream(threadId, rid)
    }
  }

  const cancelRequest = async (threadId, requestId) => {
    const ts = getThreadState(threadId)
    if (!ts || !requestId) return false
    if (
      ts.queuedRequests?.some(
        (request) => request.request_id === requestId && request.status === 'sending'
      )
    )
      return false
    try {
      await agentApi.cancelRequest(requestId)
      stopRequestStream(threadId, requestId)
      removeRequestFromQueue(ts, requestId)
      if (ts.onGoingConv?.msgChunks) {
        delete ts.onGoingConv.msgChunks[requestId]
      }
      return true
    } catch (error) {
      if (error?.name !== 'AbortError') {
        handleChatError(error, 'cancel')
      }
      return false
    }
  }

  const syncQueuedRequests = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    try {
      const resp = await agentApi.listThreadQueuedRequests(threadId, agentSlug)
      const requests = resp?.requests || []
      const knownIds = new Set(requests.map((request) => request.request_id))
      ts.queuedRequests = [
        ...requests,
        ...(ts.queuedRequests || []).filter(
          (request) => request.status === 'sending' && !knownIds.has(request.request_id)
        )
      ]
      ts.queueSnapshot = resp?.queue || { ...IDLE_QUEUE_SNAPSHOT }
    } catch (e) {
      console.warn('Failed to sync queued requests:', e)
    }
  }

  /** 同步线程队列后恢复仍在途的请求流。 */
  const resumeQueuedRequests = async (threadId, agentSlug) => {
    if (!threadId || !agentSlug) return

    await syncQueuedRequests(threadId, agentSlug)
    const latestTs = getThreadState(threadId)
    if (!latestTs) return

    for (const request of latestTs.queuedRequests || []) {
      if (request?.request_id && request.status !== 'sending') {
        void startRequestStream(threadId, request.request_id)
      }
    }
  }

  const startRequestStream = async (threadId, requestId) => {
    if (!threadId || !requestId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    ts.requestStreams = ts.requestStreams || {}
    if (ts.requestStreams[requestId]) return

    const controller = new AbortController()
    const entry = { controller, position: 0, status: 'queued' }
    ts.requestStreams[requestId] = entry

    try {
      const response = await agentApi.streamRequestEvents(requestId, {
        signal: controller.signal
      })
      if (!response.ok) {
        throw new Error(`Request SSE response not ok: ${response.status}`)
      }

      const handleEvent = (event, data) => {
        // 一次性取 ts/entry，避免每个分支重复 getThreadState 触发响应式追踪。
        const tsInner = getThreadState(threadId)
        const innerEntry = tsInner?.requestStreams?.[requestId]
        if (!tsInner || innerEntry?.controller !== controller) return

        if (event === 'queued' && data) {
          entry.position = data.position || entry.position
          const queuedRequest = tsInner.queuedRequests?.find((r) => r.request_id === requestId)
          if (queuedRequest) queuedRequest.queue_position = entry.position
        } else if (event === 'run_created' && data) {
          entry.status = 'dispatched'
          if (data.run_id) {
            const request = tsInner.queuedRequests?.find((item) => item.request_id === requestId)
            const requestMessages =
              tsInner.onGoingConv?.msgChunks?.[requestId] ||
              (request
                ? [
                    {
                      id: request.input_message_id || requestId,
                      type: 'human',
                      request_id: requestId,
                      content: request.content,
                      created_at: request.created_at
                    }
                  ]
                : null)
            removeRequestFromQueue(tsInner, requestId)
            stopRequestStream(threadId, requestId)

            // 旧 Run 尚未 finalize 时保留已渲染内容；startRunStream 会 flush 并中止旧订阅。
            // 若旧 Run 已 finalize，则其 history 刷新已在途，可以清理残留的 ongoing 状态。
            if (!tsInner.activeRunId) {
              resetOnGoingConv(threadId, { preserveRequestStreams: true })
            }
            if (requestMessages && tsInner.onGoingConv?.msgChunks) {
              tsInner.onGoingConv.msgChunks[requestId] = requestMessages
            }
            tsInner.pendingRequestId = requestId
            void startRunStream(threadId, data.run_id, '0-0', { requestId })
          }
        } else if (event === 'cancelled' || event === 'rejected' || event === 'failed') {
          entry.status = event
          tsInner.isStreaming = false
          tsInner.replyLoadingVisible = false
          tsInner.pendingRequestId = null
          delete tsInner.onGoingConv.msgChunks[requestId]
          removeRequestFromQueue(tsInner, requestId)
          stopRequestStream(threadId, requestId)
          if (typeof onStreamError === 'function') {
            onStreamError(threadId, requestId, event)
          }
        }
      }

      await processRunSseResponse(response, handleEvent)
    } catch (error) {
      if (error?.name !== 'AbortError') {
        console.error('Request SSE stream error:', error)
        handleChatError(error, 'stream')
      }
    } finally {
      const tsFinal = getThreadState(threadId)
      if (tsFinal?.requestStreams?.[requestId]?.controller === controller) {
        delete tsFinal.requestStreams[requestId]
      }
    }
  }

  const continueQueue = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || ts.continueQueueInFlight) return false

    ts.continueQueueInFlight = true
    try {
      const response = await agentApi.continueThreadQueue(threadId, agentSlug)
      await syncQueuedRequests(threadId, agentSlug)
      if (response?.request_id) {
        void startRequestStream(threadId, response.request_id)
      }
      return true
    } catch (error) {
      handleChatError(error, 'continue_queue')
      return false
    } finally {
      ts.continueQueueInFlight = false
    }
  }

  const steerRequest = async (threadId, agentSlug, requestId) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || !requestId) return false

    try {
      await agentApi.steerRequest(requestId)
      await syncQueuedRequests(threadId, agentSlug)
      void startRequestStream(threadId, requestId)
      return true
    } catch (error) {
      handleChatError(error, 'steer')
      return false
    }
  }

  return {
    startRequestStream,
    stopAllRequestStreams,
    cancelRequest,
    syncQueuedRequests,
    resumeQueuedRequests,
    continueQueue,
    steerRequest
  }
}
