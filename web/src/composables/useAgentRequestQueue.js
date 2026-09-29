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
  const isPermanentRequestError = (error) =>
    error?.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)

  const removeRequestFromQueue = (ts, requestId) => {
    if (!ts || !ts.queuedRequests) return
    ts.queuedRequests = ts.queuedRequests.filter((r) => r.request_id !== requestId)
  }

  const stopRequestStream = (threadId, requestId) => {
    const ts = getThreadState(threadId)
    if (ts?.requestRetryTimers?.[requestId]) {
      clearTimeout(ts.requestRetryTimers[requestId])
      delete ts.requestRetryTimers[requestId]
    }
    const entry = ts?.requestStreams?.[requestId]
    if (!entry) return
    entry.controller?.abort()
    delete ts.requestStreams[requestId]
  }

  const stopAllRequestStreams = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    for (const rid of Object.keys(ts.requestRetryTimers || {})) {
      stopRequestStream(threadId, rid)
    }
    for (const rid of Object.keys(ts.requestStreams || {})) {
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
        ...requests.map((request) => {
          const localMessage = ts.queuedRequests?.find(
            (item) => item.request_id === request.request_id
          )?.message
          // 队列接口只投影状态与文字，本地用户消息随队列项保留到派发或取消。
          return localMessage ? { ...request, message: localMessage } : request
        }),
        ...(ts.queuedRequests || []).filter(
          (request) => request.status === 'sending' && !knownIds.has(request.request_id)
        )
      ]
      ts.queueSnapshot = resp?.queue || { ...IDLE_QUEUE_SNAPSHOT }
    } catch (e) {
      console.warn('Failed to sync queued requests:', e)
    }
  }

  /** 为已接入请求建立订阅，在队列快照移除已派发项前接住本地消息。 */
  const subscribeQueuedRequests = (threadId) => {
    for (const request of getThreadState(threadId)?.queuedRequests || []) {
      if (request?.request_id && request.status !== 'sending') {
        void startRequestStream(threadId, request.request_id)
      }
    }
  }

  /** 同步前保留本地请求，同步后订阅服务端新增项。 */
  const resumeQueuedRequests = async (threadId, agentSlug) => {
    if (!threadId || !agentSlug) return
    subscribeQueuedRequests(threadId)
    await syncQueuedRequests(threadId, agentSlug)
    subscribeQueuedRequests(threadId)
  }

  const startRequestStream = async (threadId, requestId) => {
    if (!threadId || !requestId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    ts.requestStreams = ts.requestStreams || {}
    const message = ts.queuedRequests?.find((request) => request.request_id === requestId)?.message
    if (ts.requestStreams[requestId]) {
      ts.requestStreams[requestId].message ||= message
      return
    }
    if (ts.requestRetryTimers?.[requestId]) {
      clearTimeout(ts.requestRetryTimers[requestId])
      delete ts.requestRetryTimers[requestId]
    }

    const controller = new AbortController()
    const entry = { controller, position: 0, status: 'queued', message }
    ts.requestStreams[requestId] = entry

    const reconcileRequest = async () => {
      if (entry.status !== 'queued' || controller.signal.aborted) return
      const result = await agentApi.getRequestResult(threadId, requestId)
      if (result.run_id) {
        handleEvent('run_created', { run_id: result.run_id })
      } else if (['cancelled', 'rejected', 'failed'].includes(result.status)) {
        handleEvent(result.status, result)
      }
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
              (innerEntry.message ? [innerEntry.message] : null) ||
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

    try {
      const response = await agentApi.streamRequestEvents(threadId, requestId, {
        signal: controller.signal
      })
      if (!response.ok) {
        const error = new Error(`Request SSE response not ok: ${response.status}`)
        error.status = response.status
        throw error
      }
      await processRunSseResponse(response, handleEvent)
      await reconcileRequest()
    } catch (error) {
      if (error?.name !== 'AbortError') {
        let permanentError = isPermanentRequestError(error) ? error : null
        try {
          await reconcileRequest()
        } catch (lookupError) {
          console.warn('Failed to reconcile request after SSE error:', lookupError)
          if (isPermanentRequestError(lookupError)) permanentError = lookupError
        }
        if (entry.status === 'queued') {
          if (permanentError) {
            entry.status = 'unavailable'
            const tsInner = getThreadState(threadId)
            if (tsInner?.requestStreams?.[requestId]?.controller === controller) {
              tsInner.isStreaming = false
              tsInner.replyLoadingVisible = false
              tsInner.pendingRequestId = null
              if (tsInner.onGoingConv?.msgChunks) delete tsInner.onGoingConv.msgChunks[requestId]
              removeRequestFromQueue(tsInner, requestId)
              handleChatError(permanentError, 'stream')
              onStreamError?.(threadId, requestId, 'unavailable')
            }
          } else {
            console.warn('Request SSE disconnected; retrying from persistent status:', error)
          }
        }
      }
    } finally {
      const tsFinal = getThreadState(threadId)
      if (tsFinal?.requestStreams?.[requestId]?.controller === controller) {
        delete tsFinal.requestStreams[requestId]
      }
      if (
        entry.status === 'queued' &&
        !controller.signal.aborted &&
        tsFinal?.queuedRequests?.some((request) => request.request_id === requestId)
      ) {
        tsFinal.requestRetryTimers = tsFinal.requestRetryTimers || {}
        const timer = setTimeout(() => {
          if (tsFinal.requestRetryTimers?.[requestId] !== timer) return
          delete tsFinal.requestRetryTimers[requestId]
          if (tsFinal.queuedRequests?.some((request) => request.request_id === requestId)) {
            void startRequestStream(threadId, requestId)
          }
        }, 1000)
        tsFinal.requestRetryTimers[requestId] = timer
      }
    }
  }

  const continueQueue = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || ts.continueQueueInFlight) return false

    ts.continueQueueInFlight = true
    subscribeQueuedRequests(threadId)
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

    void startRequestStream(threadId, requestId)
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
